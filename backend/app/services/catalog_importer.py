"""Repair problem catalog importer.

The CSV at REPAIR_CATALOG_PATH is the ONLY business source for trade / problem /
complexity / duration. This module reads it with an explicit column mapping and
returns a validated, deduplicated item list plus an import summary. It never
writes to the CSV.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Internal field -> accepted source header aliases (normalized form; see _norm_header)
COLUMN_ALIASES: dict[str, list[str]] = {
    "trade_type": ["trade type", "trade", "trade_type", "category", "repair category", "工种", "维修类别", "维修类型", "类别"],
    "problem_name": ["specific problem", "problem", "problem_name", "issue", "具体问题", "问题", "故障"],
    "complexity_level": ["problem complexity", "complexity", "complexity_level", "level", "问题复杂度", "复杂度"],
    "repair_duration_minutes": [
        "repair duration (min)", "repair duration", "duration", "duration (min)", "duration_min",
        "repair_duration_minutes", "duration minutes", "维修时长", "维修时长(分钟)", "对应维修时长", "时长",
    ],
}
REQUIRED_FIELDS = tuple(COLUMN_ALIASES.keys())
# Legal complexity levels used by the source catalog (integer 1..5). Engineering default; validated on import.
ALLOWED_COMPLEXITY_LEVELS = {1, 2, 3, 4, 5}


def _norm_header(h: str) -> str:
    h = (h or "").replace("﻿", "").strip().lower()
    h = re.sub(r"\s+", " ", h)
    return h


def normalize_key(text: str) -> str:
    """Normalize trade/problem text for stable identity and alias matching."""
    t = (text or "").strip().lower()
    t = re.sub(r"[\s\-_/]+", " ", t)
    t = re.sub(r"[^\w& ']", "", t)
    return re.sub(r"\s+", " ", t).strip()


def make_catalog_item_id(trade_type: str, problem_name: str) -> str:
    digest = hashlib.sha1(f"{normalize_key(trade_type)}||{normalize_key(problem_name)}".encode()).hexdigest()
    return f"cat_{digest[:12]}"


@dataclass
class CatalogRow:
    catalog_item_id: str
    trade_type: str
    problem_name: str
    complexity_level: int
    repair_duration_minutes: int
    source_row: int


@dataclass
class ImportResult:
    ok: bool
    source_path: str
    source_file_hash: str | None = None
    catalog_version: str | None = None
    encoding: str | None = None
    delimiter: str | None = None
    actual_headers: list[str] = field(default_factory=list)
    column_mapping: dict[str, str] = field(default_factory=dict)
    items: list[CatalogRow] = field(default_factory=list)
    valid_count: int = 0
    duplicate_count: int = 0
    error_rows: list[dict[str, Any]] = field(default_factory=list)
    fatal_error: str | None = None
    trade_types: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source_path": self.source_path,
            "source_file_hash": self.source_file_hash,
            "catalog_version": self.catalog_version,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "actual_headers": self.actual_headers,
            "column_mapping": self.column_mapping,
            "valid_count": self.valid_count,
            "duplicate_count": self.duplicate_count,
            "error_rows": self.error_rows,
            "fatal_error": self.fatal_error,
            "trade_types": self.trade_types,
        }


def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # Last resort for Excel-exported files on Chinese Windows; reported in summary.
        return raw.decode("gb18030"), "gb18030"


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _map_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    mapping: dict[str, str] = {}
    missing: list[str] = []
    normalized = {_norm_header(h): h for h in headers}
    for internal, aliases in COLUMN_ALIASES.items():
        found = None
        for alias in aliases:
            if alias in normalized:
                found = normalized[alias]
                break
        if found is None:
            missing.append(internal)
        else:
            mapping[internal] = found
    return mapping, missing


def import_catalog_bytes(raw: bytes, source_path: str) -> ImportResult:
    result = ImportResult(ok=False, source_path=source_path)
    result.source_file_hash = hashlib.sha256(raw).hexdigest()
    result.catalog_version = result.source_file_hash[:12]
    if not raw.strip():
        result.fatal_error = "Catalog file is empty"
        return result
    text, encoding = _decode(raw)
    result.encoding = encoding
    delimiter = _sniff_delimiter(text[:4096])
    result.delimiter = delimiter
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration:
        result.fatal_error = "Catalog file has no header row"
        return result
    headers = [h.strip() for h in headers]
    result.actual_headers = headers
    mapping, missing = _map_columns(headers)
    result.column_mapping = mapping
    if missing:
        result.fatal_error = (
            f"Could not map required columns {missing} from actual headers {headers}. "
            "Add an explicit alias in COLUMN_ALIASES instead of guessing."
        )
        return result
    idx = {internal: headers.index(src) for internal, src in mapping.items()}

    seen: dict[str, CatalogRow] = {}
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not (c or "").strip() for c in row):
            continue
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        trade = (row[idx["trade_type"]] or "").strip()
        problem = (row[idx["problem_name"]] or "").strip()
        comp_raw = (row[idx["complexity_level"]] or "").strip()
        dur_raw = (row[idx["repair_duration_minutes"]] or "").strip()
        errors: list[str] = []
        if not trade:
            errors.append("trade_type is empty")
        if not problem:
            errors.append("problem_name is empty")
        comp: int | None = None
        try:
            comp_f = float(comp_raw)
            if comp_f != int(comp_f):
                raise ValueError
            comp = int(comp_f)
            if comp not in ALLOWED_COMPLEXITY_LEVELS:
                errors.append(f"complexity_level {comp} not in allowed levels {sorted(ALLOWED_COMPLEXITY_LEVELS)}")
        except ValueError:
            errors.append(f"complexity_level '{comp_raw}' is not an integer")
        dur: int | None = None
        try:
            dur_f = float(dur_raw)
            if dur_f != int(dur_f) or int(dur_f) <= 0:
                raise ValueError
            dur = int(dur_f)
        except ValueError:
            errors.append(f"repair_duration_minutes '{dur_raw}' is not a positive integer")
        if errors:
            result.error_rows.append({"source_row": line_no, "raw": row, "errors": errors})
            continue
        assert comp is not None and dur is not None
        item_id = make_catalog_item_id(trade, problem)
        if item_id in seen:
            prev = seen[item_id]
            if prev.complexity_level == comp and prev.repair_duration_minutes == dur:
                result.duplicate_count += 1
                continue
            result.error_rows.append({
                "source_row": line_no,
                "raw": row,
                "errors": [
                    (f"conflicts with row {prev.source_row}: same trade+problem but different "
                    f"complexity/duration ({prev.complexity_level}/{prev.repair_duration_minutes} vs {comp}/{dur})")
                ],
            })
            result.fatal_error = "Conflicting duplicate keys found; refusing to import silently"
            continue
        seen[item_id] = CatalogRow(item_id, trade, problem, comp, dur, line_no)

    result.items = list(seen.values())
    result.valid_count = len(result.items)
    result.trade_types = sorted({i.trade_type for i in result.items})
    if result.fatal_error:
        return result
    if not result.items:
        result.fatal_error = "No valid catalog rows"
        return result
    result.ok = True
    return result


def import_catalog_file(path: Path) -> ImportResult:
    if not path.exists():
        r = ImportResult(ok=False, source_path=str(path))
        r.fatal_error = f"Catalog file not found at {path}. Place the CSV there or set REPAIR_CATALOG_PATH."
        return r
    return import_catalog_bytes(path.read_bytes(), str(path))

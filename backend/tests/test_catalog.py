from pathlib import Path

from app.config import get_settings
from app.services.catalog_importer import import_catalog_bytes, import_catalog_file, make_catalog_item_id


def test_real_catalog_headers_and_counts():
    r = import_catalog_file(get_settings().catalog_path)
    assert r.ok, r.fatal_error
    assert r.actual_headers == ["Trade Type", "Specific Problem", "Problem Complexity", "Repair Duration (min)"]
    assert r.column_mapping == {"trade_type": "Trade Type", "problem_name": "Specific Problem",
                                "complexity_level": "Problem Complexity", "repair_duration_minutes": "Repair Duration (min)"}
    assert r.valid_count == 46 and r.duplicate_count == 0 and r.error_rows == []
    ids = {i.catalog_item_id for i in r.items}
    assert len(ids) == 46
    # fixed mapping: same id → same parameters
    item = next(i for i in r.items if i.trade_type == "Air Conditioning" and i.problem_name == "Compressor failure")
    assert (item.complexity_level, item.repair_duration_minutes) == (5, 105)


def test_bom_quotes_and_chinese_aliases():
    raw = "﻿工种,具体问题,问题复杂度,维修时长(分钟)\n\"Air Conditioning\",\"No cooling, or heating\",3,50\n".encode()
    r = import_catalog_bytes(raw, "fixture.csv")
    assert r.ok and r.encoding == "utf-8-sig" and r.valid_count == 1
    assert r.items[0].problem_name == "No cooling, or heating"


def test_duplicates_dedupe_but_conflicts_fail():
    dup = b"Trade Type,Specific Problem,Problem Complexity,Repair Duration (min)\nA,x,2,35\nA,x,2,35\n"
    r = import_catalog_bytes(dup, "dup.csv")
    assert r.ok and r.valid_count == 1 and r.duplicate_count == 1
    conflict = b"Trade Type,Specific Problem,Problem Complexity,Repair Duration (min)\nA,x,2,35\nA,x,3,50\n"
    r2 = import_catalog_bytes(conflict, "conflict.csv")
    assert not r2.ok and "Conflicting" in (r2.fatal_error or "")
    assert r2.error_rows and "conflicts with row 2" in r2.error_rows[0]["errors"][0]


def test_invalid_rows_reported_not_guessed():
    raw = b"Trade Type,Specific Problem,Problem Complexity,Repair Duration (min)\nA,ok,2,35\n,missing trade,2,35\nA,bad level,9,35\nA,bad dur,2,0\nA,text dur,2,abc\n"
    r = import_catalog_bytes(raw, "bad.csv")
    assert r.ok and r.valid_count == 1 and len(r.error_rows) == 4
    codes = " ".join(e for row in r.error_rows for e in row["errors"])
    assert "trade_type is empty" in codes and "not in allowed levels" in codes and "positive integer" in codes


def test_unknown_headers_are_not_guessed():
    raw = b"Col1,Col2,Col3,Col4\nA,x,2,35\n"
    r = import_catalog_bytes(raw, "unknown.csv")
    assert not r.ok and "Could not map" in (r.fatal_error or "")


def test_missing_file_reports_error(tmp_path: Path):
    r = import_catalog_file(tmp_path / "nope.csv")
    assert not r.ok and "not found" in (r.fatal_error or "")


def test_stable_id_normalization():
    assert make_catalog_item_id("Air Conditioning", "Water leakage") == make_catalog_item_id(" air  conditioning ", "WATER LEAKAGE")
    assert make_catalog_item_id("Air Conditioning", "Water leakage") != make_catalog_item_id("Refrigerator", "Water leakage")

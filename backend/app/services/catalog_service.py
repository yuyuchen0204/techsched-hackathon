from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import CatalogImport, CatalogItem
from app.services.catalog_importer import ImportResult, import_catalog_file, normalize_key


def reload_catalog(db: Session) -> ImportResult:
    """Import the configured CSV. On success replaces the active catalog (old rows deactivated)."""
    result = import_catalog_file(get_settings().catalog_path)
    db.add(CatalogImport(ok=result.ok, catalog_version=result.catalog_version, summary=result.summary()))
    if not result.ok:
        return result
    existing = {c.id: c for c in db.scalars(select(CatalogItem)).all()}
    seen: set[str] = set()
    for row in result.items:
        seen.add(row.catalog_item_id)
        item = existing.get(row.catalog_item_id)
        if item is None:
            item = CatalogItem(id=row.catalog_item_id)
            db.add(item)
        item.trade_type = row.trade_type
        item.problem_name = row.problem_name
        item.complexity_level = row.complexity_level
        item.repair_duration_minutes = row.repair_duration_minutes
        item.source_row = row.source_row
        item.source_file_hash = result.source_file_hash or ""
        item.catalog_version = result.catalog_version or ""
        item.active = True
    for item_id, item in existing.items():
        if item_id not in seen:
            item.active = False
    db.flush()
    return result


def catalog_status(db: Session) -> dict[str, Any]:
    last = db.scalars(select(CatalogImport).order_by(CatalogImport.id.desc()).limit(1)).first()
    active_count = len(list_active(db))
    return {
        "configured": active_count > 0,
        "active_count": active_count,
        "catalog_version": last.catalog_version if last else None,
        "last_import": last.summary if last else None,
        "path": get_settings().repair_catalog_path,
    }


def list_active(db: Session) -> list[CatalogItem]:
    return list(db.scalars(select(CatalogItem).where(CatalogItem.active.is_(True)).order_by(CatalogItem.source_row)).all())


def get_item(db: Session, catalog_item_id: str) -> CatalogItem | None:
    item = db.get(CatalogItem, catalog_item_id)
    return item if item and item.active else None


def snapshot_of(item: CatalogItem) -> dict[str, Any]:
    return {
        "catalog_item_id": item.id,
        "trade_type": item.trade_type,
        "problem_name": item.problem_name,
        "complexity_level": item.complexity_level,
        "repair_duration_minutes": item.repair_duration_minutes,
        "catalog_version": item.catalog_version,
        "source_row": item.source_row,
    }


def search(db: Session, query: str, limit: int = 10) -> list[CatalogItem]:
    q = normalize_key(query)
    items = list_active(db)
    if not q:
        return items[:limit]
    tokens = [t for t in q.split(" ") if t]
    scored: list[tuple[int, CatalogItem]] = []
    for it in items:
        hay = f"{normalize_key(it.trade_type)} {normalize_key(it.problem_name)}"
        score = sum(1 for t in tokens if t in hay)
        if score:
            scored.append((score, it))
    scored.sort(key=lambda x: (-x[0], x[1].source_row))
    return [it for _, it in scored[:limit]]

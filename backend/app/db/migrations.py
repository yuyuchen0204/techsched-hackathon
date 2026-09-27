"""Additive, idempotent migrations for existing SQLite databases.

create_all() creates new tables; ALTER TABLE ADD COLUMN backfills new columns on existing tables. Applied versions are
recorded in schema_migrations. Never drops data; backups: copy data/app.db before upgrading (see README → Database).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

# version -> list of (table, column, DDL type/default)
COLUMN_MIGRATIONS: dict[str, list[tuple[str, str, str]]] = {
    "2026-09-16-v3-orders": [
        ("work_orders", "customer_id", "VARCHAR(32)"),
        ("work_orders", "address", "JSON DEFAULT '{}'"),
        ("work_orders", "report_status", "VARCHAR(20) DEFAULT 'none'"),
        ("work_orders", "excluded_technician_ids", "JSON DEFAULT '[]'"),
        ("work_orders", "human_case_id", "VARCHAR(32)"),
        ("work_orders", "expedite_event_key", "VARCHAR(160)"),
    ],
    "2026-09-16-v3-technicians": [
        ("technicians", "sim_mode", "VARCHAR(10) DEFAULT 'auto'"),
        ("technicians", "demo_login", "VARCHAR(40)"),
    ],
    "2026-09-18-v3-expedite": [
        ("work_orders", "expedite_state", "JSON DEFAULT '{}'"),
    ],
    # "send someone now" is not the same as "pay for a busy slot" — it needs its own fact on the order
    "2026-09-20-v31-expedite-now": [
        ("work_orders", "expedite_now", "BOOLEAN DEFAULT 0"),
    ],
    # Agent-to-agent delegation + the reasoning behind each tool call, which used to be discarded with the Action.
    "2026-09-24-agent-collaboration": [
        ("agent_tasks", "parent_task_id", "VARCHAR(32)"),
        ("agent_tasks", "delegation_depth", "INTEGER DEFAULT 0"),
        ("agent_tasks", "child_task_ids", "JSON DEFAULT '[]'"),
        ("agent_tasks", "waiting_child_id", "VARCHAR(32)"),
        ("tool_traces", "thought", "TEXT"),
    ],
}


def migrate(engine: Engine) -> list[str]:
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(40) PRIMARY KEY, applied_at DATETIME)"))
        done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations")).all()}
        insp = inspect(engine)
        for version, cols in COLUMN_MIGRATIONS.items():
            if version in done:
                continue
            for table, column, ddl in cols:
                if table not in insp.get_table_names():
                    continue
                existing = {c["name"] for c in insp.get_columns(table)}
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    log.info("migration %s: added %s.%s", version, table, column)
            conn.execute(text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                         {"v": version, "t": datetime.now(UTC).replace(tzinfo=None)})
            applied.append(version)
        # data migration: legacy fixed-lunch breaks stay as declared BreakBlocks only if they were real confirmed breaks;
        # the seed's uniform 12:00 template is dropped by the V3 seed. Existing orders get a minimal structured address
        # marked 'migrated' with unit pending (never invented).
        if "2026-09-16-v3-address-backfill" not in done and "work_orders" in insp.get_table_names():
            rows = conn.execute(text("SELECT id, location_name, lat, lon, address FROM work_orders")).all()
            for oid, loc_name, lat, lon, addr in rows:
                if addr in (None, "{}", "null"):
                    payload = json.dumps({"formatted_address": loc_name or "", "latitude": lat, "longitude": lon, "source": "migrated",
                                          "unit_number": None, "unit_pending": True})
                    conn.execute(text("UPDATE work_orders SET address = :a WHERE id = :i"), {"a": payload, "i": oid})
            conn.execute(text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
                         {"v": "2026-09-16-v3-address-backfill", "t": datetime.now(UTC).replace(tzinfo=None)})
            applied.append("2026-09-16-v3-address-backfill")
    return applied


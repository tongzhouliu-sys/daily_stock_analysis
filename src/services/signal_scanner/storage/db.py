from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.services.signal_scanner.core import DomainError, canonical, stamp, uid

IMMUTABLE = (
    "bar_revisions",
    "market_event_revisions",
    "rule_events",
    "signal_cards",
    "account_snapshots",
    "ledger_entries",
)


def row(value):
    if value is None:
        return None
    d = dict(value)
    for k, v in d.items():
        if k.endswith("_json") and isinstance(v, str):
            d[k] = json.loads(v)
    return d


def many(cx, sql, args=()):
    return [row(r) for r in cx.execute(sql, args).fetchall()]


def one(cx, sql, args=()):
    return row(cx.execute(sql, args).fetchone())


def insert(cx, table, values):
    columns = {r[1] for r in cx.execute(f"PRAGMA table_info({table})")}
    if not columns or set(values) - columns:
        raise ValueError(f"Invalid columns for {table}: {set(values) - columns}")
    data = {"created_at": stamp(), **values}
    data = {k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in data.items()}
    cx.execute(
        f"INSERT INTO {table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})", tuple(data.values())
    )


def update(cx, table, key, value, changes):
    changes = {**changes, "updated_at": stamp()}
    data = {k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in changes.items()}
    cx.execute(
        f"UPDATE {table} SET {','.join(k + '=?' for k in data)} WHERE {key}=?", (*data.values(), value)
    )


def audit(cx, action, object_id, reason="", **details):
    insert(
        cx,
        "audit_events",
        dict(
            id=uid("audit"),
            actor_id="owner",
            actor_kind="USER",
            action=action,
            object_type=details.pop("object_type", "BUSINESS"),
            object_id=object_id,
            occurred_at=stamp(),
            reason=reason,
            redacted_details_json=details,
        ),
    )


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self, *, readonly=False):
        cx = sqlite3.connect(
            f"file:{self.path}?mode=ro" if readonly else self.path,
            uri=readonly,
            isolation_level=None,
            timeout=5,
        )
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA foreign_keys=ON")
        cx.execute("PRAGMA busy_timeout=5000")
        if readonly:
            cx.execute("PRAGMA query_only=ON")
        return cx

    def migrate(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as cx:
            cx.execute("PRAGMA journal_mode=WAL")
            cx.executescript(Path(__file__).with_name("schema.sql").read_text())
            for table in IMMUTABLE:
                cx.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table} BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'IMMUTABLE_VERSION'); END"
                )
        os.chmod(self.path, 0o600)

    @contextmanager
    def read(self):
        cx = self.connect(readonly=True)
        try:
            cx.execute("BEGIN")
            yield cx
        finally:
            cx.rollback()
            cx.close()

    @contextmanager
    def transaction(self):
        cx = self.connect()
        try:
            cx.execute("BEGIN IMMEDIATE")
            yield cx
            cx.commit()
        except sqlite3.OperationalError as e:
            cx.rollback()
            if "locked" in str(e):
                raise DomainError("DATABASE_BUSY", "数据库繁忙，请使用原幂等键重试", 503) from None
            raise
        except Exception:
            cx.rollback()
            raise
        finally:
            cx.close()

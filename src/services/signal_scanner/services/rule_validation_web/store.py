"""Independent control plane. Reads never initialize or migrate storage."""

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.services.signal_scanner.core import canonical, digest, require, stamp, uid

ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def identifier(value):
    require(bool(ID.fullmatch(value)), "RESOURCE_NOT_FOUND", "资源不存在", 404)
    return value


def safe_path(root, value):
    root, value = Path(root).absolute(), Path(value).absolute()
    require(value.is_relative_to(root), "INPUT_PATH_FORBIDDEN", "目录不在登记范围")
    for part in [root, *value.relative_to(root).parents]:
        candidate = part if part.is_absolute() else root / part
        require(not candidate.is_symlink(), "INPUT_PATH_FORBIDDEN", "不允许符号链接")
    require(
        not value.is_symlink() and value.resolve().is_relative_to(root.resolve()),
        "INPUT_PATH_FORBIDDEN",
        "不允许符号链接或目录越界",
    )
    return value


class Store:
    def __init__(self, root, input_roots=()):
        self.root = Path(root).absolute()
        self.input_roots = tuple(Path(p).absolute() for p in input_roots)
        self.path = self.root / "control.sqlite3"

    @classmethod
    def configured(cls, runtime):
        return cls(
            os.environ.get("RULE_VALIDATION_WEB_ROOT") or Path(runtime) / "rule-validation",
            filter(None, os.environ.get("RULE_VALIDATION_INPUT_ROOTS", "").split(os.pathsep)),
        )

    @property
    def ready(self):
        return self.path.is_file()

    def initialize(self):
        require(not self.root.is_symlink(), "OUTPUT_PATH_FORBIDDEN")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.path) as cx:
            cx.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS objects_kind ON objects(kind,id);
            CREATE TABLE IF NOT EXISTS idempotency(scope TEXT,key TEXT,hash TEXT,response TEXT,
              PRIMARY KEY(scope,key));
            CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor TEXT, action TEXT,
              resource TEXT,input_digest TEXT,at TEXT);
            PRAGMA user_version=1;
            """)

    @contextmanager
    def read(self):
        require(self.ready, "RESEARCH_NOT_INITIALIZED", "研究目录尚未初始化", 503)
        cx = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        cx.execute("PRAGMA query_only=ON")
        try:
            yield cx
        finally:
            cx.close()

    @contextmanager
    def write(self):
        require(self.ready, "RESEARCH_NOT_INITIALIZED", "请先初始化独立研究目录", 503)
        cx = sqlite3.connect(self.path, timeout=15)
        try:
            cx.execute("BEGIN IMMEDIATE")
            yield cx
            cx.commit()
        except BaseException:
            cx.rollback()
            raise
        finally:
            cx.close()

    def get(self, kind, id, cx=None):
        identifier(id)
        if cx is None:
            with self.read() as conn:
                return self.get(kind, id, conn)
        row = cx.execute("SELECT payload FROM objects WHERE kind=? AND id=?", (kind, id)).fetchone()
        require(row, "RESOURCE_NOT_FOUND", "资源不存在", 404)
        return json.loads(row[0])

    def all(self, kind, cx=None):
        if not self.ready:
            return []
        if cx is None:
            with self.read() as conn:
                return self.all(kind, conn)
        return [
            json.loads(r[0])
            for r in cx.execute("SELECT payload FROM objects WHERE kind=? ORDER BY id DESC", (kind,))
        ]

    def put(self, kind, item, cx):
        cx.execute(
            "INSERT INTO objects VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (kind, item["id"], canonical(item)),
        )

    def mutate(self, scope, key, payload, fn):
        require(key and len(key) <= 200, "MALFORMED_REQUEST", "缺少幂等请求标识", 400)
        h = digest(payload)
        with self.write() as cx:
            old = cx.execute(
                "SELECT hash,response FROM idempotency WHERE scope=? AND key=?", (scope, key)
            ).fetchone()
            if old:
                require(old[0] == h, "IDEMPOTENCY_INPUT_CONFLICT", "同一请求标识对应了不同输入")
                return json.loads(old[1])
            result = fn(cx)
            cx.execute("INSERT INTO idempotency VALUES(?,?,?,?)", (scope, key, h, canonical(result)))
            cx.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?,?)",
                (
                    uid(),
                    "owner",
                    scope,
                    result.get("run_id", result.get("validation_id", result.get("comparison_id", ""))),
                    h,
                    stamp(),
                ),
            )
            return result


def public(item):
    return {
        k: v
        for k, v in item.items()
        if k
        not in {
            "index_fingerprint",
            "manifest_path",
            "source_root",
            "report_ref",
            "index_path",
            "fingerprints",
            "template",
            "snapshot_path",
            "chart_index_path",
            "chart_source_path",
            "chart_index_fingerprint",
            "chart_source_fingerprint",
            "staging_paths",
        }
    }

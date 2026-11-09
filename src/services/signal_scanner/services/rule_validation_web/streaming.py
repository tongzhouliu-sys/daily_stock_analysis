"""Bounded-memory equivalent of report business/file hash verification.

Values are decoded one array item at a time, canonicalized to private temporary
spools, then hashed in sorted top-level order. This also accepts whitespace and
unordered top-level keys without assuming the source file is canonical JSON.
"""

import hashlib
import json
import tempfile
from pathlib import Path

from src.services.signal_scanner.core import canonical, reject_credentials, require
from src.services.signal_scanner.research.contracts import semantic_manifest

from .catalog import file_hash

ARRAYS = {
    "aggregation",
    "assessments",
    "benchmarks",
    "daily",
    "events",
    "input_references",
    "outcomes",
    "statistics",
    "transitions",
    "unconditional",
}
PUBLIC_ARRAYS = {"assessments", "benchmarks", "events", "outcomes", "transitions", "unconditional"}
FILES = {"report.json", "report.md"} | {
    s + ".csv"
    for s in ("events", "transitions", "outcomes", "statistics", "benchmarks", "unconditional", "assessments")
}


class Reader:
    def __init__(self, stream):
        self.stream, self.buffer, self.position, self.eof = stream, "", 0, False
        self.decoder = json.JSONDecoder()

    def more(self):
        self.buffer = self.buffer[self.position :]
        self.position = 0
        block = self.stream.read(65536)
        self.eof = not block
        self.buffer += block
        require(len(self.buffer) <= 32 * 1024 * 1024, "REPORT_ROW_TOO_LARGE")

    def peek(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if self.eof:
                return ""
            self.more()

    def take(self, expected):
        require(self.peek() == expected, "ARTIFACT_CORRUPT")
        self.position += 1

    def value(self):
        require(self.peek(), "ARTIFACT_CORRUPT")
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
                # A number/string may end exactly at a chunk boundary. Read its delimiter first.
                if (end == len(self.buffer) and not self.eof) or (
                    end < len(self.buffer) and self.buffer[end] not in ",]}: \t\r\n"
                ):
                    require(not self.eof, "ARTIFACT_CORRUPT")
                    self.more()
                    continue
                self.position = end
                return value
            except json.JSONDecodeError:
                require(not self.eof, "ARTIFACT_CORRUPT")
                self.more()

    def array(self):
        self.take("[")
        if self.peek() != "]":
            while True:
                yield self.value()
                if self.peek() == "]":
                    break
                self.take(",")
        self.take("]")

    def members(self):
        self.take("{")
        seen = set()
        if self.peek() != "}":
            while True:
                key = self.value()
                require(isinstance(key, str) and key not in seen, "ARTIFACT_CORRUPT")
                seen.add(key)
                self.take(":")
                yield key
                if self.peek() == "}":
                    break
                self.take(",")
        self.take("}")


def verified_stream(path, cx, redact):
    path = Path(path)
    manifest = json.loads((path.parent / "files.json").read_text())
    require(set(manifest) == FILES, "ARTIFACT_CORRUPT")
    for name, expected in manifest.items():
        p = path.parent / name
        require(p.is_file() and not p.is_symlink() and file_hash(p) == expected, "ARTIFACT_CORRUPT")
    meta, spools, intervals, flags = {}, {}, [], set()
    cx.execute("CREATE TEMP TABLE canonical_evidence(id TEXT PRIMARY KEY,payload TEXT)")
    with tempfile.TemporaryDirectory(
        dir=Path(cx.execute("PRAGMA database_list").fetchone()[2]).parent
    ) as work:
        with path.open() as stream:
            reader = Reader(stream)
            for key in reader.members():
                part = Path(work) / str(len(spools))
                spools[key] = part
                with part.open("w") as spool:
                    if key in ARRAYS:
                        spool.write("[")
                        for n, row in enumerate(reader.array()):
                            reject_credentials(row)
                            if n:
                                spool.write(",")
                            spool.write(canonical(row))
                            if key in PUBLIC_ARRAYS:
                                cx.execute("INSERT INTO rows VALUES(?,?,?)", (key, n, canonical(redact(row))))
                            if key == "events":
                                intervals.append((row["trigger_market_date"], row["trigger_market_date"]))
                            if key in ("transitions", "daily", "assessments"):
                                intervals.append(
                                    (row.get("from_market_date", row["market_date"]), row["market_date"])
                                )
                            if key in ("statistics", "unconditional") and row.get("split") == "SEALED":
                                flags.add("SEALED_STATISTIC")
                            if (
                                key == "outcomes"
                                and row.get("split") == "SEALED"
                                and row.get("status") != "EXCLUDED"
                            ):
                                flags.add("SEALED_OUTCOME")
                            if key == "benchmarks" and (row.get("benchmark") or {}).get("split") == "SEALED":
                                flags.add("SEALED_BENCHMARK")
                        spool.write("]")
                    elif key == "confirmation_evidence":
                        for eid in reader.members():
                            value = reader.value()
                            reject_credentials(value)
                            cx.execute("INSERT INTO canonical_evidence VALUES(?,?)", (eid, canonical(value)))
                            cx.execute("INSERT INTO evidence VALUES(?,?)", (eid, canonical(redact(value))))
                        spool.write("{")
                        for n, (eid, payload) in enumerate(
                            cx.execute("SELECT id,payload FROM canonical_evidence ORDER BY id")
                        ):
                            if n:
                                spool.write(",")
                            spool.write(canonical(eid) + ":" + payload)
                        spool.write("}")
                    else:
                        value = reader.value()
                        reject_credentials(value)
                        meta[key] = value
                        spool.write(canonical(semantic_manifest(value) if key == "input_manifest" else value))
            require(not reader.peek(), "ARTIFACT_CORRUPT")
        h = hashlib.sha256()
        h.update(b"{")
        for n, key in enumerate(sorted(set(spools) - {"business_output_hash", "generated_at", "execution"})):
            if n:
                h.update(b",")
            h.update((canonical(key) + ":").encode())
            with spools[key].open("rb") as part:
                for block in iter(lambda: part.read(1024 * 1024), b""):
                    h.update(block)
        h.update(b"}")
        require(
            meta.get("business_output_hash") == h.hexdigest() and path.parent.name == h.hexdigest(),
            "ARTIFACT_CORRUPT",
        )
    require(meta.get("schema_version") == 1, "UNSUPPORTED_VERSION", "当前版本暂不支持该报告")
    sealed = meta["identity"]["split"]["sets"].get("SEALED")
    if sealed and not meta["identity"]["sealed_revealed"]:
        require(
            not flags and not any(not (end < sealed[0] or start > sealed[1]) for start, end in intervals),
            "SEALED_DATA_LEAK",
            "未揭示报告包含封存内容",
        )
        cx.execute("DELETE FROM rows WHERE json_extract(payload,'$.split')='SEALED'")
    cx.execute("DROP TABLE canonical_evidence")
    return meta

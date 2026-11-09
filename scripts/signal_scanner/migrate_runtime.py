"""Copy a stopped scanner's business data into a new DSA runtime atomically.

Credentials, browser sessions, process locks and old backup archives stay in the
source. Stop all source writers first; this command never alters the source.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source, target):
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
        with sqlite3.connect(target) as new:
            old.backup(new)
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError(f"Database integrity check failed: {source.name}")
    target.chmod(0o600)


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination must not exist; existing data is never overwritten")
    if destination.is_relative_to(source):
        raise ValueError("Destination must be outside the source runtime")
    if not (source / "scanner.sqlite3").is_file():
        raise ValueError("Source scanner.sqlite3 is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".scanner-migration-", dir=destination.parent))
    try:
        backup_database(source / "scanner.sqlite3", stage / "scanner.sqlite3")
        with sqlite3.connect(stage / "scanner.sqlite3") as cx:
            if cx.execute("SELECT count(*) FROM job_runs WHERE status='RUNNING'").fetchone()[0]:
                raise ValueError("Source contains running jobs; finish them before migration")
            cx.execute("DELETE FROM auth_sessions")
            tables = [row[0] for row in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {table: cx.execute('SELECT count(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
                      for table in tables}
        manifest = {}
        for name in ("artifacts", "imports", "daily-market", "rule-validation"):
            root = source / name
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Symlink cannot be migrated: {path.relative_to(source)}")
                if not path.is_file() or path.suffix in (".lock", ".pid") or path.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                relative = path.relative_to(source)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if path.suffix == ".sqlite3":
                    backup_database(path, target)
                else:
                    shutil.copy2(path, target)
                    original_hash = file_hash(path)
                    copied_hash = file_hash(target)
                    if original_hash != copied_hash:
                        raise ValueError(f"File changed during migration: {relative}")
                    manifest[str(relative)] = copied_hash
        report = {"source": str(source), "destination": str(destination), "table_counts": counts,
                  "verified_files": manifest, "auth": "DSA session only"}
        (stage / "migration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        os.rename(stage, destination)
        return {"destination": str(destination), "table_counts": counts, "verified_files": len(manifest)}
    except BaseException:
        shutil.rmtree(stage)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination), ensure_ascii=False, indent=2))

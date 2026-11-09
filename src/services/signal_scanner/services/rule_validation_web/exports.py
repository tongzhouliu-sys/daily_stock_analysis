import csv
import hashlib
import os

from src.services.signal_scanner.core import canonical, require

from .catalog import file_hash
from .query import SECTIONS, connect, raw_rows, summary
from .store import safe_path


def safe_cell(value):
    if isinstance(value, (dict, list)):
        value = canonical(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        try:
            from decimal import Decimal

            Decimal(value)
        except (ValueError, ArithmeticError):
            value = "'" + value
    return value


def publish_exports(store, run):
    folder = store.root / "exports" / run["id"]
    safe_path(store.root, folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for existing in folder.iterdir():
        require(not existing.is_symlink(), "OUTPUT_PATH_FORBIDDEN")
    info = summary(run)
    meta = {
        "kind": "WEB_DERIVED_REPORT",
        "source_business_hash": run["report_hash"],
        "query_version": "web-stats-1",
        "summary": info,
    }
    with (folder / "report.json").open("w") as f:
        f.write(canonical(meta)[:-1])
        for section in SECTIONS:
            f.write(',"' + section + '":[')
            for n, row in enumerate(raw_rows(run, section)):
                if n:
                    f.write(",")
                f.write(canonical(row))
            f.write("]")
        f.write("}")
    (folder / "report.md").write_text(
        "# 规则验证 Web 派生报告\n\n源业务 hash："
        + run["report_hash"]
        + "\n\nSYNTHETIC 为合成工程数据；完成不代表规则有效或获批准。\n\n"
        + "\n\n".join(info["limitations"])
        + "\n\n协议与覆盖：\n\n```json\n"
        + canonical(info)
        + "\n```\n"
    )
    for section in (*SECTIONS, "statistics"):
        rows = (r for r in raw_rows(run, section)) if section != "statistics" else iter([])
        if section == "statistics":
            from src.services.signal_scanner.research.statistics import summarize

            rows = iter(summarize(list(raw_rows(run, "outcomes")), info["review"]["min_completed_samples"]))
        # Discover columns independently; no report is buffered for downloads.
        if section != "statistics":
            columns = sorted({k for r in raw_rows(run, section) for k in r})
        else:
            rows = list(rows)
            columns = sorted({k for r in rows for k in r})
        with (folder / (section + ".csv")).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["source_business_hash", *columns])
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {"source_business_hash": run["report_hash"], **{k: safe_cell(v) for k, v in row.items()}}
                )
    run["artifacts"] = [
        {
            "name": p.name,
            "sha256": file_hash(p),
            "size_bytes": p.stat().st_size,
            "source_business_hash": run["report_hash"],
            "derived": True,
        }
        for p in sorted(folder.iterdir())
    ]


def artifact(store, run, name):
    with connect(run):
        pass
    match = next((a for a in run.get("artifacts", []) if a["name"] == name), None)
    require(match, "RESOURCE_NOT_FOUND", "附件不存在", 404)
    folder = store.root / "exports" / run["id"]
    p = safe_path(folder, folder / name)
    require(p.is_file(), "ARTIFACT_CORRUPT", "附件不存在")
    # Verify and stream the same open descriptor; replacement/symlink races cannot change the download.
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    stream = os.fdopen(fd, "rb")
    try:
        require(
            hashlib.file_digest(stream, "sha256").hexdigest() == match["sha256"],
            "ARTIFACT_CORRUPT",
            "附件校验失败",
        )
        stream.seek(0)
    except BaseException:
        stream.close()
        raise
    return stream, match

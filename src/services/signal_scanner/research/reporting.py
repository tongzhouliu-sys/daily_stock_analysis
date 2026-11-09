import csv
import hashlib
import io
import json
import os
from pathlib import Path

from src.services.signal_scanner.core import canonical, digest, require


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not path.is_symlink(), "OUTPUT_PATH_FORBIDDEN")
    tmp = path.with_name("." + path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def business_hash(report):
    payload = {
        k: v for k, v in report.items() if k not in ("business_output_hash", "generated_at", "execution")
    }
    if "input_manifest" in payload:
        from src.services.signal_scanner.research.contracts import semantic_manifest

        payload["input_manifest"] = semantic_manifest(payload["input_manifest"])
    return digest(payload)


def csv_text(rows):
    if not rows:
        return ""
    columns = sorted(set().union(*(r.keys() for r in rows)))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    for r in rows:
        values = {k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in r.items()}
        # Avoid spreadsheet formula execution when the research CSV is opened in Excel.
        for k, v in values.items():
            if isinstance(v, str) and v.startswith(("=", "+", "@")):
                values[k] = "'" + v
        writer.writerow(values)
    return out.getvalue()


def markdown(report):
    identity = report["identity"]
    lines = [
        "# 规则回放与验证报告",
        "",
        f"输入哈希：`{report['input_hash']}`",
        "",
        f"业务输出哈希：`{report['business_output_hash']}`",
        "",
        f"数据：{report['data_kind']}；模式：{identity['research_mode']}；区间：{identity['start_date']} 至 {identity['end_date']}",
        "",
        "## 实验与输入",
        "",
        "```json",
        canonical(identity),
        "```",
        "",
        "## 规则生命周期",
        "",
        "| 规则 | 触发 | 确认 | 失效 | 到期 | 阻断 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in report["lifecycle_counts"]:
        lines.append(
            f"| {r['rule_id']} | {r['triggered']} | {r['confirmed']} | {r['invalidated']} | {r['expired']} | {r['blocked']} |"
        )
    lines += [
        "",
        "## 分布与基准",
        "",
        "统计包含毛/净收益均值、p05/p25/p50/p75/p95、实际分母及低样本标记。",
        "",
        "完整分组见 [statistics.csv](statistics.csv)；事件见 [events.csv](events.csv)；生命周期见 [transitions.csv](transitions.csv)；",
        "[outcomes.csv](outcomes.csv)、[benchmarks.csv](benchmarks.csv)、[unconditional.csv](unconditional.csv) 分别保存标签、配对基准与无条件基准。",
        "",
        "## 数据质量与覆盖",
        "",
        "```json",
        canonical(report["coverage"]),
        "```",
        "",
        "## 偏差与限制",
        "",
    ]
    lines += ["- " + x for x in report["biases"] + report["limitations"]]
    lines += [
        "",
        "## 评审建议",
        "",
        "```json",
        canonical(report["review"]),
        "```",
        "",
        "全部输入引用、分组、成本、协议和业务时间保存在 [report.json](report.json)。报告无在线依赖。",
        "",
    ]
    return "\n".join(lines)


def load_report(runtime, ref):
    root = Path(runtime).resolve()
    path = (root / ref).resolve()
    require(path.is_relative_to(root) and path.is_file(), "REPORT_NOT_READY")
    report = json.loads(path.read_text())
    require(report["business_output_hash"] == business_hash(report), "ARTIFACT_CORRUPT")
    require(path.parent.name == report["business_output_hash"], "ARTIFACT_CORRUPT")
    index = json.loads((path.parent / "files.json").read_text())
    required = {"report.json", "report.md"} | {
        name + ".csv"
        for name in (
            "events",
            "transitions",
            "outcomes",
            "statistics",
            "benchmarks",
            "unconditional",
            "assessments",
        )
    }
    require(set(index) == required, "ARTIFACT_CORRUPT")
    for name, expected in index.items():
        file = (path.parent / name).resolve()
        require(file.parent == path.parent and file.is_file(), "ARTIFACT_CORRUPT")
        require(hashlib.sha256(file.read_bytes()).hexdigest() == expected, "ARTIFACT_CORRUPT")
    return report


def publish(runtime, report):
    report["business_output_hash"] = business_hash(report)
    folder = Path(runtime) / "reports" / report["business_output_hash"]
    if (folder / "report.json").is_file() and (folder / "files.json").is_file():
        load_report(runtime, str((folder / "report.json").relative_to(runtime)))
        return str((folder / "report.json").relative_to(runtime))
    for key in (
        "events",
        "transitions",
        "outcomes",
        "statistics",
        "benchmarks",
        "unconditional",
        "assessments",
    ):
        atomic_text(folder / (key + ".csv"), csv_text(report[key]))
    atomic_text(folder / "report.json", canonical(report))
    atomic_text(folder / "report.md", markdown(report))
    names = ["report.json", "report.md"] + [
        name + ".csv"
        for name in (
            "events",
            "transitions",
            "outcomes",
            "statistics",
            "benchmarks",
            "unconditional",
            "assessments",
        )
    ]
    index = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}
    atomic_text(folder / "files.json", canonical(index))
    return str((folder / "report.json").relative_to(runtime))

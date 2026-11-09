import argparse
import json
import sys
from pathlib import Path

from src.services.signal_scanner.core import DomainError, canonical, require
from src.services.signal_scanner.research.experiments import compare_reports
from src.services.signal_scanner.research.fixtures import create_fixture
from src.services.signal_scanner.research.reporting import atomic_text, business_hash, load_report
from src.services.signal_scanner.research.runner import run, validate_input
from src.services.signal_scanner.storage.db import Database, many


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线规则回放与验证（不联网，不写生产业务表）")
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("fixture", help="生成明确标记SYNTHETIC的工程输入，不是真实市场数据")
    fixture.add_argument("--output", required=True)
    fixture.add_argument("--symbols", type=int, default=2)
    fixture.add_argument("--sessions", type=int, default=252)
    val = sub.add_parser("validate")
    val.add_argument("manifest")
    start = sub.add_parser("run")
    start.add_argument("manifest")
    start.add_argument("--runtime", required=True)
    start.add_argument("--reveal-sealed", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--runtime", required=True)
    report = sub.add_parser("report")
    report.add_argument("--runtime", required=True)
    report.add_argument("--job-id", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("left")
    compare.add_argument("right")
    compare.add_argument("--axis", choices=["rules", "universe", "data", "price_basis"], required=True)
    compare.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fixture":
            require(1 <= args.symbols <= 300 and 1 <= args.sessions <= 2520, "INVALID_FIXTURE_SIZE")
            result = dict(
                manifest=create_fixture(args.output, symbols=args.symbols, sessions=args.sessions),
                data_kind="SYNTHETIC",
            )
        elif args.command == "validate":
            m, s, _ = validate_input(args.manifest)
            result = dict(
                status="VALID",
                experiment_id=m["experiment_id"],
                data_kind=s["data_kind"],
                research_mode=m["research_mode"],
            )
        elif args.command == "run":
            result = run(args.manifest, args.runtime, reveal=args.reveal_sealed)
        elif args.command in ("status", "report"):
            root = Path(args.runtime).resolve()
            require((root / ".research-runtime.json").is_file(), "NOT_RESEARCH_RUNTIME")
            db = Database(root / "scanner.sqlite3")
            with db.read() as cx:
                rows = many(
                    cx,
                    "SELECT id,status,progress_json,error_json,output_manifest_ref FROM job_runs WHERE job_name='RULE_VALIDATION' ORDER BY created_at,id",
                )
            if args.command == "status":
                result = dict(items=rows)
            else:
                rows = [r for r in rows if r["id"] == args.job_id]
                require(rows, "JOB_NOT_FOUND")
                ref = rows[0]["output_manifest_ref"]
                require(ref, "REPORT_NOT_READY")
                path = (root / ref).resolve()
                require(path.is_relative_to(root), "OUTPUT_PATH_FORBIDDEN")
                result = load_report(root, ref)
        else:
            reports = [json.loads(Path(p).read_text()) for p in (args.left, args.right)]
            for r in reports:
                require(r["business_output_hash"] == business_hash(r), "ARTIFACT_CORRUPT")
            result = compare_reports(*reports, args.axis)
            atomic_text(args.output, canonical(result))
        print(canonical(result))
        return 0
    except DomainError as ex:
        print(canonical(dict(status="FAILED", code=ex.code, details=ex.details)), file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        print(canonical(dict(status="FAILED", code="INVALID_INPUT_OR_IO")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

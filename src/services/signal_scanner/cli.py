from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from datetime import timedelta
from pathlib import Path

from src.services.signal_scanner.core import Settings, canonical, digest, instant, now, require, stamp, uid
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.services.auth import Auth
from src.services.signal_scanner.services.config import initialize, get_config
from src.services.signal_scanner.storage.db import Database, insert, one
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.adapters.market import ingest


def setup(settings):
    db = Database(settings.db_path)
    db.migrate()
    with db.transaction() as cx:
        initialize(cx)
    Auth(settings).provision()
    (settings.runtime / "imports").mkdir(exist_ok=True, mode=0o700)
    return db


def activate(cx, key, value):
    from src.services.signal_scanner.services.config import DEFAULTS

    old = get_config(cx, key)
    at = stamp()
    n = one(cx, "SELECT COUNT(*) AS n FROM config_versions WHERE config_key=?", (key,))["n"]
    cx.execute(
        "UPDATE config_versions SET effective_until=? WHERE config_key=? AND version=?",
        (at, key, old["version"]),
    )
    insert(
        cx,
        "config_versions",
        dict(
            id=uid("cfg"),
            config_key=key,
            config_type=DEFAULTS[key][0],
            version=f"{key}-v{n + 1}",
            effective_from=at,
            payload_json=value,
            content_hash=digest(value),
            change_reason="显式本地导入/验证配置",
            actor="CLI",
        ),
    )


def fixture(settings):
    """Explicitly fictional dataset, always a separate runtime directory."""
    setup(settings)
    cal = Calendar()
    end = cal.last_completed(now())
    days = cal.dates("2024-01-01", end)[-340:]
    items = [
        dict(
            id=s,
            ticker=s,
            name="虚构验证数据 · " + s,
            type="ETF" if s in ("SPY", "QQQ", "IWM", "XLK") else "COMMON",
            currency="USD",
            venue="XNYS",
            sector="TECH" if s in ("DEMO", "DEMO2") else None,
            roles=["TRADE"] if s in ("DEMO", "DEMO2") else ["MARKET"],
            profile=dict(
                market_cap="10000000000",
                sector_etf_id="XLK",
                market_ref_id="SPY",
                concentration_bucket="INDEX",
                comparators=["QQQ", "IWM"],
            ),
        )
        for s in ("DEMO", "DEMO2", "SPY", "QQQ", "IWM", "XLK")
    ]
    bars = []
    for item in items:
        for n, d in enumerate(days):
            price = 100 + n * 0.2 if item["id"] in ("DEMO", "DEMO2") else 300 + n * 0.35
            if n == len(days) - 1 and item["id"] in ("DEMO", "DEMO2"):
                price += 8
            bars.append(
                dict(
                    instrument_id=item["id"],
                    market_date=d,
                    open=f"{price - 0.7:.2f}",
                    high=f"{price + 1:.2f}",
                    low=f"{price - 1:.2f}",
                    close=f"{price:.2f}",
                    volume="6000000" if n == len(days) - 1 else "1000000",
                    is_final=True,
                )
            )
    pack = dict(
        provider="FIXTURE",
        feed="FICTIONAL_ONLY",
        coverage="FICTIONAL",
        instruments=items,
        bars=bars,
        fictional=True,
    )
    path = settings.runtime / "imports" / "market.json"
    path.write_text(canonical(pack))
    os.chmod(path, 0o600)
    ev = dict(
        events=[
            dict(
                event_id="fixture-vix-" + d,
                event_type="VIX_VALUE",
                known_at=stamp(instant(cal.session(d)["close_at"]) + timedelta(minutes=1)),
                scheduled_date=d,
                status="PUBLISHED",
                source="FICTIONAL",
                payload=dict(value="18", market_date=d),
            )
            for d in days
        ],
        security_statuses=[dict(instrument_id=i["id"], status="ACTIVE", observed_at=stamp()) for i in items],
    )
    (settings.runtime / "imports" / "events.json").write_text(canonical(ev))
    db = Database(settings.db_path)
    with db.transaction() as cx:
        ingest(cx, pack, cal, Artifacts(settings.runtime / "artifacts"))
        activate(cx, "universe", dict(members=["DEMO", "DEMO2"]))
        activate(
            cx,
            "provider",
            dict(
                mode="LOCAL_FILE",
                file="market.json",
                feed="FICTIONAL_ONLY",
                capabilities=[
                    dict(
                        capability_id="RTH_DAILY",
                        support="SUPPORTED",
                        entitlement="GRANTED",
                        enabled=True,
                        verified_at=stamp(),
                        evidence_ref="FICTIONAL_GENERATED_ONLY",
                    )
                ],
                quote_enabled=False,
            ),
        )
        activate(
            cx, "events", dict(mode="LOCAL_FILE", file="events.json", coverage="OK", verified_at=stamp())
        )
    from src.services.signal_scanner.worker.jobs import Worker, enqueue

    with db.transaction() as cx:
        enqueue(cx, "DAILY_SCAN", "FIXTURE_SCAN:" + uid(), end)
    w = Worker(settings)
    while w.run_one():
        pass
    print("虚构验证环境已建立：", settings.runtime)


def restore(source, target):
    target = Path(target).resolve()
    require(
        not target.exists() or not any(target.iterdir()), "OUT_OF_RANGE", "恢复目标必须为新建或空目录", 422
    )
    require(not (target / "scanner.sqlite3").exists(), "OUT_OF_RANGE", "恢复目标不得覆盖现有数据库", 422)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with zipfile.ZipFile(source) as z:
        manifest = json.loads(z.read("manifest.json"))
        for info in z.infolist():
            dest = (target / info.filename).resolve()
            require(dest.is_relative_to(target), "OUT_OF_RANGE", status=422)
            require(not ((info.external_attr >> 16) & 0o170000) == 0o120000, "OUT_OF_RANGE", status=422)
        require(hashlib.sha256(z.read("scanner.sqlite3")).hexdigest() == manifest["sha256"], "BACKUP_INVALID")
        for f in manifest["files"]:
            require(hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"], "BACKUP_INVALID")
        z.extractall(target)
    db = Database(target / "scanner.sqlite3")
    with db.transaction() as cx:
        require(cx.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_INVALID")
        cx.execute("UPDATE auth_sessions SET revoked_at=?", (stamp(),))
        cx.execute(
            "UPDATE notifications SET status='SUPPRESSED' WHERE status IN ('PENDING','SENDING','FAILED')"
        )
        cx.execute("UPDATE job_runs SET status='QUEUED',replay=1,lease_owner=NULL WHERE status='RUNNING'")
    Auth(Settings(target)).provision()
    print("已校验并恢复到：", target)


def main():
    parser = argparse.ArgumentParser(description="交易信号扫描器：不执行订单")
    parser.add_argument("--runtime", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("worker")
    sub.add_parser("fixture")
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, required=True)
    im = sub.add_parser("ingest")
    im.add_argument("file", type=Path)
    im.add_argument("--activate", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("job", choices=["DAILY_SCAN", "STATUS", "DIGEST", "WEEKLY_REPORT", "BACKUP"])
    run.add_argument("--date")
    rs = sub.add_parser("restore")
    rs.add_argument("archive", type=Path)
    rs.add_argument("target", type=Path)
    args = parser.parse_args()
    settings = Settings.load()
    configured_runtime = settings.runtime
    if args.runtime:
        settings.runtime = args.runtime.resolve()
        os.environ["SCANNER_RUNTIME"] = str(settings.runtime)
    if args.command == "restore":
        restore(args.archive, args.target)
        return
    if args.command == "fixture":
        if not args.runtime:
            settings.runtime = configured_runtime.parent / "signal-scanner-fixture"
        require(settings.runtime != configured_runtime, "OUT_OF_RANGE", "虚构验证数据不得写入默认运行目录", 422)
        fixture(settings)
        return
    db = setup(settings)
    if args.command == "init":
        print("初始化完成。首次密码保存在：", settings.runtime / "首次登录密码.txt")
    elif args.command == "serve":
        import uvicorn

        from src.services.signal_scanner.main import create_app

        if not os.environ.get("SCANNER_ORIGIN"):
            settings.origin = f"http://127.0.0.1:{args.port}"
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, access_log=False)
    elif args.command == "worker":
        from src.services.signal_scanner.worker.jobs import Worker

        Worker(settings).run()
    elif args.command == "ingest":
        bundle = json.loads(args.file.read_text())
        with db.transaction() as cx:
            result = ingest(cx, bundle, Calendar(), Artifacts(settings.runtime / "artifacts"))
            if args.activate:
                activate(
                    cx,
                    "universe",
                    dict(
                        members=[
                            i["id"] for i in bundle["instruments"] if "TRADE" in i.get("roles", ["TRADE"])
                        ]
                    ),
                )
                import shutil

                dest = settings.runtime / "imports" / "market.json"
                shutil.copyfile(args.file, dest)
                os.chmod(dest, 0o600)
                activate(
                    cx,
                    "provider",
                    dict(
                        mode="LOCAL_FILE",
                        file="market.json",
                        feed=bundle["feed"],
                        capabilities=[],
                        quote_enabled=False,
                    ),
                )
        print(canonical(result))
    elif args.command == "run":
        from src.services.signal_scanner.worker.jobs import Worker, enqueue

        with db.transaction() as cx:
            jid = enqueue(
                cx,
                args.job,
                "MANUAL:" + args.job + ":" + uid(),
                args.date or Calendar().last_completed(now()),
            )
        worker = Worker(settings)
        while worker.run_one():
            pass
        with db.read() as cx:
            print(canonical(one(cx, "SELECT id,status,error_json FROM job_runs WHERE id=?", (jid,))))


if __name__ == "__main__":
    main()

from src.services.signal_scanner.platform_support import locking as fcntl
import json
import sqlite3
import threading
import time
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from src.services.signal_scanner.core import DomainError, canonical, digest, instant, require, stamp, uid
from src.services.signal_scanner.domain.features import adjust
from src.services.signal_scanner.domain.rules import RuleEngine
from src.services.signal_scanner.domain.scoring import environment
from src.services.signal_scanner.research.benchmarks import benchmarks
from src.services.signal_scanner.research.contracts import input_hash, validate
from src.services.signal_scanner.research.experiments import assignment, register, split_manifest
from src.services.signal_scanner.research.inputs import (
    FeatureCursor,
    InputView,
    ResearchCalendar,
    active_members,
    attributes,
    clock_for,
    effective_actions,
    read_snapshot,
    validate_snapshot,
)
from src.services.signal_scanner.research.isolation import offline
from src.services.signal_scanner.research.outcomes import event_outcomes
from src.services.signal_scanner.research.reporting import atomic_text, load_report, publish
from src.services.signal_scanner.research.statistics import lifecycle_counts, summarize
from src.services.signal_scanner.research.versions import HASH_VERSION, STATISTICS_VERSION, code_hash, ruleset
from src.services.signal_scanner.storage.db import Database, insert, one, update
from src.services.signal_scanner.storage.files import Artifacts


def _gate(s, iid, day, clock, env, events, cal):
    reasons = []
    statuses = [
        x
        for x in s.get("security_statuses", [])
        if x["instrument_id"] == iid
        and x["from"] <= day <= x["to"]
        and instant(x["known_at"]) <= instant(clock)
    ]
    if not statuses:
        reasons.append("SECURITY_STATUS_UNVERIFIED")
    elif max(statuses, key=lambda x: x["known_at"])["status"] != "ACTIVE":
        reasons.append("SECURITY_UNAVAILABLE")
    coverage = [
        x
        for x in s.get("event_coverage", [])
        if x["from"] <= day <= x["to"] and instant(x["known_at"]) <= instant(clock) and x["status"] == "OK"
    ]
    if not coverage or max(coverage, key=lambda x: x["known_at"])["status"] != "OK":
        reasons.append("EVENT_COVERAGE_UNVERIFIED")
    if env not in ("NORMAL", "STRESS"):
        reasons.append("ENVIRONMENT_" + env)
    for e in events:
        if e.get("instrument_id") == iid and e["event_type"] == "EARNINGS":
            scheduled = e.get("scheduled_date")
            if not scheduled:
                reasons.append("EARNINGS_TIME_UNVERIFIED")
                continue
            event_day = cal.cal.date_to_session(scheduled, direction="next").strftime("%Y-%m-%d")
            start = instant(cal.session(cal.shift(event_day, -1))["open_at"])
            published = e.get("published_at")
            end = (
                (instant(cal.session(cal.first_full_after(published))["close_at"]) + timedelta(minutes=30))
                if published
                else None
            )
            if instant(clock) >= start and (end is None or instant(clock) < end):
                reasons.append("EARNINGS_WINDOW")
    return sorted(set(reasons))


def _rebase(engine, bars, actions, clock, day):
    if not actions:
        return
    for e in engine.events:
        if e["lifecycle"] not in ("PENDING", "CONFIRMED") and not (
            e["rule_id"] in ("TR-01", "VP-01")
            and e.get("confirmation_market_date")
            and engine.cal.distance(e["confirmation_market_date"], day) <= 10
        ):
            continue
        ref = next((b for b in bars if b["market_date"] == e["trigger_market_date"]), None)
        if not ref:
            continue
        factor = float(adjust([ref, {**bars[-1]}], actions, clock)[0]["price_factor"])
        old = float(e["frozen"].get("price_factor", 1))
        if factor == old:
            continue
        ratio = factor / old
        e.setdefault("original_frozen", deepcopy(e["frozen"]))
        for key in (
            "close",
            "high",
            "low",
            "atr",
            "entry_anchor",
            "level",
            "pullback_low",
            "pullback_high",
            "confirmation_atr",
        ):
            if e["frozen"].get(key) is not None:
                e["frozen"][key] *= ratio
        e["frozen"]["price_factor"] = str(factor)
        e.setdefault("unit_conversions", []).append(
            dict(
                market_date=day,
                factor=ratio,
                action_refs=[dict(event_id=a["event_id"], revision=a["revision"]) for a in actions],
            )
        )


def replay(m, s, cal, progress=None, *, reveal=False):
    view = InputView(s, m, cal)
    split = split_manifest(m, cal)
    ids = sorted({x["instrument_id"] for x in s["universe"]})
    benchmark_ids = {m["benchmark_ref"]["market"]}
    benchmark_ids.update(i.get("sector_etf_id") for i in s["instruments"] if i.get("sector_etf_id"))
    allids = sorted(set(ids) | benchmark_ids)
    origins = [b["market_date"] for rows in s["bars"].values() for b in rows]
    require(origins, "NO_INPUT_BARS")
    first = min(origins)
    # Warmup length is bounded by a fixed origin, not a moving 260-row truncation.
    require(first <= cal.shift(m["start_date"], -m["warmup_bars"]), "INSUFFICIENT_WARMUP")
    through = min(cal.shift(m["end_date"], 3), cal.last_completed(instant(m["label_as_of"])))
    require(through >= m["start_date"], "INVALID_LABEL_CUTOFF")
    if not reveal and "SEALED" in split["sets"]:
        through = min(through, cal.shift(split["sets"]["SEALED"][0], -1))
    days = cal.dates(first, through)
    engines = {iid: RuleEngine(cal, ruleset(m["parameter_overrides"])) for iid in ids}
    cursors = {iid: FeatureCursor(cal, iid) for iid in allids}
    assessments = []
    last_assessments = {}

    def record_assessment(row):
        day = row["market_date"]
        if day > m["end_date"]:
            return
        key = (row["instrument_id"], row["rule_id"])
        signature = digest({k: v for k, v in row.items() if k != "market_date"})
        prior = last_assessments.get(key)
        if prior and prior[0] == signature and cal.distance(prior[1]["market_date"], day) == 1:
            prior[1]["market_date"] = day
            prior[1]["count"] += 1
        else:
            item = {**row, "from_market_date": day, "count": 1}
            assessments.append(item)
            last_assessments[key] = (signature, item)

    daily = []
    used = {}
    quality = Counter()
    previous_env = None
    event_attrs = {}
    event_gates = {}
    confirm_features = {}
    total = len(days) * len(allids)
    processed = 0
    for day in days:
        clock = clock_for(day, m, cal)
        if instant(clock) > instant(m["label_as_of"]):
            break
        ev = view.events(clock)
        by_instrument = {}
        for event in ev:
            if event.get("instrument_id"):
                by_instrument.setdefault(event["instrument_id"], []).append(event)
        market_refs = [
            dict(event_id=e["event_id"], revision=e["revision"])
            for e in ev
            if e["event_type"] == "MARKET_HALT"
            or (e["event_type"] == "VIX_VALUE" and e["payload_json"].get("market_date") == day)
        ]
        features = {}
        day_errors = {}
        for iid in allids:
            bars = view.bars(iid, day, clock)
            error = view.quality(bars, day)
            if not error:
                try:
                    actions = effective_actions(by_instrument.get(iid, []), iid, clock)
                except DomainError:
                    actions = []
                    error = "CORPORATE_ACTION_TIME_UNVERIFIED"
                try:
                    f, _ = cursors[iid].feature(bars, actions, clock)
                    features[iid] = f
                    if day >= m["start_date"] and f["count"] < m["warmup_bars"]:
                        error = "INSUFFICIENT_HISTORY"
                    if iid in engines:
                        _rebase(engines[iid], bars, actions, clock, day)
                except (ValueError, KeyError, ZeroDivisionError):
                    error = "BASIS_MISMATCH"
            if error:
                day_errors[iid] = error
            if day >= m["start_date"]:
                used[(iid, day)] = dict(
                    instrument_id=iid,
                    market_date=day,
                    evaluation_clock=clock,
                    prefix_hash=view.prefix_hash(iid, bars),
                    bar_refs=[dict(market_date=b["market_date"], revision=b["revision"]) for b in bars[-1:]],
                    event_refs=market_refs
                    + [
                        dict(event_id=e["event_id"], revision=e["revision"])
                        for e in by_instrument.get(iid, [])
                    ],
                )
            processed += 1
        vix = next(
            (
                e
                for e in reversed(ev)
                if e["event_type"] == "VIX_VALUE" and e["payload_json"].get("market_date") == day
            ),
            None,
        )
        env = environment(
            features.get(m["benchmark_ref"]["market"]),
            float(vix["payload_json"]["value"]) if vix else None,
            previous_env,
            any(
                e["event_type"] == "MARKET_HALT"
                and e.get("status") == "ACTIVE"
                and e["payload_json"].get("from", day) <= day <= e["payload_json"].get("to", day)
                for e in ev
            ),
        )
        previous_env = env
        members = active_members(s, day, clock)
        visible = assignment(split, day) != "SEALED" or reveal
        for iid, engine in engines.items():
            if iid not in members and not any(
                e["lifecycle"] in ("PENDING", "CONFIRMED") for e in engine.events
            ):
                continue
            if iid in day_errors:
                engine.skip(day, clock, day_errors[iid])
                quality[day_errors[iid]] += 1
                if day >= m["start_date"] and visible:
                    for r in engine.registry:
                        record_assessment(
                            dict(
                                instrument_id=iid,
                                market_date=day,
                                rule_id=r,
                                status="NOT_EVALUABLE",
                                reason_code=day_errors[iid],
                            )
                        )
                continue
            f = features[iid]
            for historical in engine.events:
                if historical["rule_id"] in ("TR-01", "VP-01") and historical.get("confirmed_at"):
                    if f["close"] < historical["frozen"]["level"]:
                        historical["structure_broken"] = True
            before = {e["event_id"]: e.get("confirmed_at") for e in engine.events}
            detect = iid in members and day <= m["end_date"]
            engine.step(
                f,
                [e for e in by_instrument.get(iid, []) if e["event_type"] == "EARNINGS"],
                detect=detect,
            )
            if day >= m["start_date"] and visible:
                for rule, assessment in engine.evaluability.items():
                    record_assessment(dict(instrument_id=iid, market_date=day, rule_id=rule, **assessment))
            for e in engine.events:
                if e["event_id"] not in event_gates:
                    event_gates[e["event_id"]] = _gate(
                        s, iid, day, clock, env["mode"], by_instrument.get(iid, []), cal
                    )
                for transition in e["state_history"]:
                    if transition["market_date"] == day:
                        transition.setdefault("evaluated_at", clock)
                if e.get("confirmed_at") and not before.get(e["event_id"]):
                    attrs = attributes(s, iid, day, clock)
                    event_attrs[e["event_id"]] = dict(
                        environment=env["mode"],
                        sector=attrs.get("sector", "UNKNOWN"),
                        sector_etf_id=attrs.get("sector_etf_id"),
                        asset_type=attrs["asset_type"],
                    )
                    event_gates[e["event_id"]] = _gate(
                        s, iid, day, clock, env["mode"], by_instrument.get(iid, []), cal
                    )
                    confirm_features[e["event_id"]] = deepcopy(f)
        if day >= m["start_date"] and visible:
            daily.append(
                dict(
                    market_date=day,
                    evaluation_clock=clock,
                    environment=env,
                    members=len(members),
                    available=len(set(features) & members),
                    failures=day_errors,
                )
            )
        if progress:
            progress(dict(processed=processed, total=total, market_date=day, failed=sum(quality.values())))
    events = []
    for engine in engines.values():
        for e in engine.events:
            if not m["start_date"] <= e["trigger_market_date"] <= m["end_date"]:
                continue
            if not reveal and assignment(split, e["trigger_market_date"]) == "SEALED":
                continue
            if (
                e.get("confirmation_market_date")
                and not reveal
                and assignment(split, e["confirmation_market_date"]) == "SEALED"
            ):
                continue
            e["initial_expiry_market_date"] = e["expiry_market_date"]
            e["expiry_market_date"] = e["valid_until"][:10]
            e["invalidated_market_date"] = next(
                (t["market_date"] for t in reversed(e["state_history"]) if t["lifecycle"] == "INVALIDATED"),
                None,
            )
            e["expired_market_date"] = next(
                (t["market_date"] for t in reversed(e["state_history"]) if t["lifecycle"] == "EXPIRED"), None
            )
            e["blocked_reasons"] = event_gates.get(e["event_id"], [])
            e["effective_grade_cap"] = "TRACK" if e["rule_id"] == "MO-01" else None
            e["observation_only"] = e["direction"] == "BEARISH" or e["kind"] != "DIRECTION"
            events.append(e)
    events.sort(key=lambda e: (e["instrument_id"], e["trigger_market_date"], e["rule_id"]))
    grouped = {}
    for e in events:
        if e["kind"] != "DIRECTION" or not e.get("confirmed_at") or e["lifecycle"] == "RETRACTED":
            continue
        key = (e["instrument_id"], e["direction"], e["family"], e["trigger_market_date"])
        grouped.setdefault(key, []).append(e)
    groups = []
    for key, es in sorted(grouped.items()):
        primary = sorted(es, key=lambda e: (e["rule_id"] != "VP-01", e["rule_id"]))[0]
        groups.append(
            dict(
                economic_event_id="economic_" + digest(key),
                primary=primary,
                rule_refs=[
                    dict(rule_id=e["rule_id"], rule_version=e["rule_version"], event_id=e["event_id"])
                    for e in es
                ],
                evidence_groups=["PRICE"],
                attributes=event_attrs[primary["event_id"]],
            )
        )
    rows = event_outcomes(groups, m, view, cal, split)
    for r in rows:
        if not reveal and r["split"] == "SEALED":
            r.update(
                status="EXCLUDED", excluded_reason="SEALED_NOT_REVEALED", gross_return=None, net_return=None
            )
    paired, unconditional = benchmarks(rows, {**m, "sealed_revealed": reveal}, view, cal, split, progress)
    identity = {
        k: m[k]
        for k in (
            "ruleset_version",
            "universe_version",
            "calendar_version",
            "algorithm_version",
            "engine_version",
            "code_hash",
            "dependency_hash",
            "start_date",
            "end_date",
            "research_mode",
            "entry_bases",
            "cost_assumption",
            "price_basis",
        )
    }
    identity.update(
        snapshot_hash=m["data_snapshot_ref"]["sha256"],
        registration=m["registration"],
        evaluation_clock=m["evaluation_clock"],
        label_as_of=m["label_as_of"],
        split=split,
        sealed_revealed=reveal,
        hash_version=HASH_VERSION,
        statistics_version=STATISTICS_VERSION,
    )
    limitations = [
        "固定会话后续分布不等同于实际成交、止损或组合策略收益。",
        "同证券重叠窗口、同日同业样本及5/10/20期结果相关；结果为探索性描述。",
        "报告不执行规则批准；真实时间4–8周观察与人工评审须另行完成。",
    ]
    if s["data_kind"] == "SYNTHETIC":
        limitations.append("SYNTHETIC：全部为合成工程样本，不能证明真实市场有效性。")
    if split["kind"] == "EXPLORATORY":
        limitations.append("合格区间不足五年，未进行60/20/20封存研究。")
    if not reveal and split.get("sets", {}).get("SEALED"):
        limitations.append("封存段未揭示，报告不包含其事件、标签或环境明细。")
    return dict(
        kind="RULE_VALIDATION_REPORT",
        schema_version=1,
        identity=identity,
        input_hash=input_hash(m, s),
        data_kind=s["data_kind"],
        input_manifest=deepcopy(m),
        input_references=list(used.values()),
        events=events,
        transitions=[dict(event_id=e["event_id"], **t) for e in events for t in e["state_history"]],
        aggregation=[{k: v for k, v in g.items() if k != "primary"} for g in groups],
        lifecycle_counts=lifecycle_counts(events, assessments),
        outcomes=rows,
        statistics=summarize(rows, m["min_completed_samples"]),
        benchmarks=paired,
        unconditional=unconditional,
        assessments=assessments,
        daily=daily,
        confirmation_evidence={
            e["event_id"]: confirm_features[e["event_id"]] for e in events if e.get("confirmed_at")
        },
        coverage=dict(
            expected_instruments=len(ids),
            available_instruments=sum(bool(s["bars"].get(i)) for i in ids),
            failed_security_sessions=sum(quality.values()),
            failure_reasons=dict(sorted(quality.items())),
        ),
        biases=[
            "幸存者偏差："
            + (
                "已冻结历史成员"
                if s.get("historical_membership_verified")
                else "当前池回填历史，无法排除幸存者偏差"
            ),
            "分析师资料不进入规则回放；缺少历史可知证据的资料不得回填。",
            "REVISED_HISTORY使用冻结的修订历史及显式可知时间假设。"
            if m["research_mode"] == "REVISED_HISTORY"
            else "POINT_IN_TIME按逐时点revision与可见时间证据选择输入。",
        ],
        limitations=limitations,
        review=dict(
            status="PENDING_REVIEW",
            recommendation="继续观察",
            automatic_approval=False,
            reasons=["工程测试不替代真实历史与实时观察；仍需人工研究评审。"],
            completed_economic_events=len(
                {r["economic_event_id"] for r in rows if r["status"] == "COMPLETED"}
            ),
            min_completed_samples=m["min_completed_samples"],
        ),
    )


def validate_input(manifest_path):
    cal = ResearchCalendar()
    path = Path(manifest_path).resolve()
    m = validate(json.loads(path.read_text()), cal)
    s = validate_snapshot(read_snapshot(path.parent, m["data_snapshot_ref"]), m, cal)
    return m, s, cal


def run(
    manifest_path, runtime, *, reveal=False, web_progress=False, cancellation_requested=None, web_attempt=1
):
    m, s, cal = validate_input(manifest_path)
    root = Path(runtime).absolute()
    require(not root.is_symlink(), "OUTPUT_PATH_FORBIDDEN")
    root = root.resolve()
    require(not Path(manifest_path).resolve().is_relative_to(root), "INPUT_OUTPUT_OVERLAP")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = root / ".research-runtime.json"
    require(marker.is_file() or not any(root.iterdir()), "NOT_RESEARCH_RUNTIME")
    if not marker.exists():
        atomic_text(marker, canonical(dict(kind="OFFLINE_RESEARCH_RUNTIME", version=1)))
    require(json.loads(marker.read_text()).get("kind") == "OFFLINE_RESEARCH_RUNTIME", "NOT_RESEARCH_RUNTIME")
    with (root / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with offline(root) as isolation:
            db = Database(root / "scanner.sqlite3")
            db.migrate()
            files = Artifacts(root / "artifacts")
            ih = digest([input_hash(m, s), reveal])
            with db.transaction() as cx:
                old = one(
                    cx,
                    "SELECT * FROM job_runs WHERE logical_key=? ORDER BY revision DESC LIMIT 1",
                    (m["logical_key"],),
                )
                if old:
                    require(old["input_hash"] == ih, "IDEMPOTENCY_INPUT_CONFLICT")
                    if old["status"] in ("SUCCEEDED", "PARTIAL"):
                        ref = old["output_manifest_ref"]
                        report = load_report(root, ref)
                        return dict(
                            id=old["id"],
                            status=old["status"],
                            report_ref=ref,
                            business_output_hash=report["business_output_hash"],
                            reused=True,
                        )
                    jid = old["id"]
                    update(
                        cx,
                        "job_runs",
                        "id",
                        jid,
                        dict(status="RUNNING", attempt_count=old["attempt_count"] + 1),
                    )
                else:
                    jid = uid("research")
                    insert(
                        cx,
                        "job_runs",
                        dict(
                            id=jid,
                            logical_key=m["logical_key"],
                            revision=m["revision"],
                            job_name="RULE_VALIDATION",
                            artifact_kind="REPORT",
                            ruleset_hash=m["ruleset_version"],
                            input_hash=ih,
                            input_manifest_ref=files.save(m),
                            status="RUNNING",
                            replay=1,
                            attempt_count=1,
                        ),
                    )
            began = time.monotonic()
            last = [0.0]
            bridge_lock = threading.Lock()

            def progress(value):
                require(not cancellation_requested or not cancellation_requested(), "RESEARCH_CANCELLED")
                if time.monotonic() - last[0] >= 1 or value["processed"] == value["total"]:
                    with db.transaction() as cx:
                        update(cx, "job_runs", "id", jid, dict(progress_json=value, heartbeat_at=stamp()))
                    if web_progress:
                        with bridge_lock:
                            atomic_text(
                                root / "web-progress" / (digest(m["logical_key"]) + ".json"),
                                canonical(
                                    dict(
                                        engine_job_id=jid, heartbeat_at=stamp(), attempt=web_attempt, **value
                                    )
                                ),
                            )
                    last[0] = time.monotonic()

            stopped = threading.Event()

            def heartbeat():
                while not stopped.wait(3):
                    try:
                        with offline(root):
                            with db.transaction() as cx:
                                update(cx, "job_runs", "id", jid, dict(heartbeat_at=stamp()))
                            if web_progress:
                                with bridge_lock:
                                    p = root / "web-progress" / (digest(m["logical_key"]) + ".json")
                                    prior = (
                                        json.loads(p.read_text()) if p.is_file() else dict(engine_job_id=jid)
                                    )
                                    if prior.get("attempt") != web_attempt:
                                        prior = dict(
                                            engine_job_id=jid, phase="REPLAY", processed=None, total=None
                                        )
                                    atomic_text(
                                        p,
                                        canonical({**prior, "heartbeat_at": stamp(), "attempt": web_attempt}),
                                    )
                    except sqlite3.OperationalError:
                        continue

            heart = threading.Thread(target=heartbeat, daemon=True)
            heart.start()
            try:
                require(not cancellation_requested or not cancellation_requested(), "RESEARCH_CANCELLED")
                registration = register(root, m, split_manifest(m, cal), reveal)
                frozen = root / "frozen" / ih
                source = (
                    (Path(manifest_path).resolve().parent / m["data_snapshot_ref"]["path"])
                    .read_bytes()
                    .decode()
                )
                atomic_text(frozen / m["data_snapshot_ref"]["path"], source)
                atomic_text(frozen / "manifest.json", canonical(m))
                source_root = Path(__file__).resolve().parents[1]
                for source_file in sorted(p for p in source_root.rglob("*") if p.suffix in (".py", ".sql")):
                    atomic_text(
                        root
                        / "source"
                        / m["code_hash"]
                        / "src"
                        / "services"
                        / "signal_scanner"
                        / source_file.relative_to(source_root),
                        source_file.read_text(),
                    )
                for name in ("requirements.lock", "pyproject.toml"):
                    atomic_text(
                        root / "source" / m["code_hash"] / "src" / "services" / "signal_scanner" / "upstream" / name, (source_root / "upstream" / name).read_text()
                    )
                from src.services.signal_scanner.research.versions import dependency_manifest

                atomic_text(root / "source" / m["code_hash"] / "runtime-dependencies.json", canonical(dependency_manifest()))
                require(code_hash() == m["code_hash"], "VERSION_UNAVAILABLE")
                report = replay(m, s, cal, progress, reveal=reveal)
                report["generated_at"] = stamp()
                report["execution"] = dict(
                    elapsed_seconds=round(time.monotonic() - began, 3),
                    network_attempts=isolation["network_attempts"],
                    subprocess_attempts=isolation["subprocess_attempts"],
                    forbidden_writes=isolation["forbidden_writes"],
                    registration=registration,
                )
                progress(dict(processed=0, total=1, phase="REPORT_PUBLICATION"))
                ref = publish(root, report)
                status = "PARTIAL" if report["coverage"]["failed_security_sessions"] else "SUCCEEDED"
                with db.transaction() as cx:
                    update(
                        cx,
                        "job_runs",
                        "id",
                        jid,
                        dict(
                            status=status,
                            output_manifest_ref=ref,
                            heartbeat_at=stamp(),
                            progress_json=dict(processed=1, total=1, phase="COMPLETED"),
                        ),
                    )
                return dict(
                    id=jid,
                    status=status,
                    report_ref=ref,
                    business_output_hash=report["business_output_hash"],
                    reused=False,
                )
            except BaseException as ex:
                with db.transaction() as cx:
                    update(
                        cx,
                        "job_runs",
                        "id",
                        jid,
                        dict(
                            status="FAILED",
                            error_json={
                                "code": ex.code if isinstance(ex, DomainError) else "INTERNAL_RESEARCH_ERROR"
                            },
                        ),
                    )
                raise
            finally:
                stopped.set()
                heart.join(timeout=5)

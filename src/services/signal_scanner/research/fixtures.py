"""Deterministic SYNTHETIC engineering data; never used as real research evidence."""

import hashlib
import math
from datetime import timedelta
from pathlib import Path

from src.services.signal_scanner.core import canonical, instant, stamp
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.domain.features import ALGORITHM
from src.services.signal_scanner.research.reporting import atomic_text
from src.services.signal_scanner.research.versions import (
    ENGINE_VERSION,
    code_hash,
    dependency_hash,
    rules_version,
)


def create_fixture(output, *, symbols=2, sessions=252, end="2026-10-23", mode="POINT_IN_TIME"):
    cal = Calendar()
    out = Path(output).resolve()
    first = cal.shift(end, -sessions + 1)
    origin = cal.shift(first, -280)
    tail = cal.shift(end, 24)
    days = cal.dates(origin, tail)
    ids = [f"SYN{i:03}" for i in range(symbols)]
    allids = ids + ["SPY", "SYN_SECTOR"]
    instruments = [
        dict(
            id=iid,
            asset_type="ETF" if iid in ("SPY", "SYN_SECTOR") else "COMMON",
            currency="USD",
            sector="SYNTHETIC",
            sector_etf_id="SYN_SECTOR",
            history=[
                dict(
                    **{"from": origin, "to": tail},
                    known_at=cal.session(origin)["open_at"],
                    sector="SYNTHETIC",
                    sector_etf_id="SYN_SECTOR",
                    asset_type="COMMON" if iid in ids else "ETF",
                )
            ],
        )
        for iid in allids
    ]
    bars = {}
    for n, iid in enumerate(allids):
        rows = []
        for i, d in enumerate(days):
            close = 100 + n * 4 + i * 0.04 + math.sin(i / 8) * 2 + (3 if i % 87 >= 80 else 0)
            o = close - 0.3
            volume = 3000000 if i % 87 == 80 else 1000000 + (i % 5) * 10000
            rows.append(
                dict(
                    market_date=d,
                    revision=1,
                    session_kind="RTH_DAILY",
                    bar_end_at=cal.session(d)["close_at"],
                    available_at=stamp(instant(cal.session(d)["close_at"]) + timedelta(minutes=10)),
                    available_at_kind="PROVIDER",
                    is_final=True,
                    quality_status="OK",
                    open=f"{o:.6f}",
                    high=f"{close + 0.4:.6f}",
                    low=f"{o - 0.4:.6f}",
                    close=f"{close:.6f}",
                    volume=str(volume),
                )
            )
        bars[iid] = rows
    snapshot = dict(
        schema_version=1,
        data_kind="SYNTHETIC",
        session_kind="RTH_DAILY",
        rth_evidence="SYNTHETIC_CALENDAR_FIXTURE",
        universe_version="synthetic-universe-1",
        calendar_version=cal.version,
        price_return_kind="PRICE_RETURN",
        adjustment_epoch="synthetic-no-actions-1",
        historical_membership_verified=True,
        historical_classification_verified=True,
        instruments=instruments,
        bars=bars,
        universe=[
            dict(instrument_id=iid, **{"from": origin, "to": tail}, known_at=cal.session(origin)["open_at"])
            for iid in ids
        ],
        events=[
            dict(
                event_id="vix-" + d,
                revision=1,
                event_type="VIX_VALUE",
                instrument_id=None,
                status="ACTIVE",
                known_at=stamp(instant(cal.session(d)["close_at"]) + timedelta(minutes=10)),
                payload_json=dict(market_date=d, value="18"),
            )
            for d in days
        ],
        security_statuses=[
            dict(
                instrument_id=iid,
                **{"from": origin, "to": tail},
                status="ACTIVE",
                known_at=cal.session(origin)["open_at"],
            )
            for iid in allids
        ],
        event_coverage=[
            dict(**{"from": origin, "to": tail}, known_at=cal.session(origin)["open_at"], status="OK")
        ],
    )
    content = canonical(snapshot)
    ref = dict(path="snapshot.json", sha256=hashlib.sha256(content.encode()).hexdigest())
    manifest = dict(
        schema_version=1,
        experiment_id=f"synthetic-{symbols}-{sessions}",
        revision=1,
        logical_key=f"synthetic-{symbols}-{sessions}",
        actor="local-researcher",
        ruleset_version=rules_version(),
        universe_version=snapshot["universe_version"],
        calendar_version=cal.version,
        data_snapshot_ref=ref,
        start_date=first,
        end_date=end,
        warmup_bars=260,
        evaluation_clock=dict(kind="SESSION_CLOSE_PLUS_MINUTES", minutes=30),
        cost_assumption=dict(
            version="two-sided-slippage-fees-v1",
            currency="USD",
            slippage_bps=[5, 10, 20],
            fee_unit="BPS_OF_ENTRY",
            entry_fee="0",
            exit_fee="0",
        ),
        price_basis=dict(
            kind="ADJUSTED", adjustment_epoch=snapshot["adjustment_epoch"], return_kind="PRICE_RETURN"
        ),
        benchmark_ref=dict(market="SPY", version="synthetic-benchmarks-1"),
        research_mode=mode,
        label_as_of=stamp(instant(cal.session(tail)["close_at"]) + timedelta(minutes=30)),
        entry_bases=["NEXT_AVAILABLE_OPEN", "CONFIRM_CLOSE"],
        split_policy=dict(kind="CHRONOLOGICAL_60_20_20", embargo_sessions=20)
        if (
            __import__("datetime").date.fromisoformat(end) - __import__("datetime").date.fromisoformat(first)
        ).days
        >= 365 * 5
        else dict(kind="EXPLORATORY"),
        min_completed_samples=30,
        algorithm_version=ALGORITHM,
        engine_version=ENGINE_VERSION,
        code_hash=code_hash(),
        dependency_hash=dependency_hash(),
        registration=dict(
            id=f"synthetic-protocol-{symbols}-{sessions}",
            registered_at="2026-10-25T00:00:00.000Z",
            hypothesis="SYNTHETIC工程确定性与边界验证",
            sealed_policy="EXPLICIT_REVEAL_ONCE",
            allowed_variations={},
        ),
        parameter_overrides={},
    )
    atomic_text(out / "snapshot.json", content)
    atomic_text(out / "manifest.json", canonical(manifest))
    return str(out / "manifest.json")

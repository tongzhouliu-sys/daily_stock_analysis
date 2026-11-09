from collections import defaultdict
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from src.services.signal_scanner.core import dec, digest, instant, text_decimal


def morning_metrics(decisions, start, end, window, config_version):
    first = {}
    for d in sorted(decisions, key=lambda x: (x["server_time"], x["id"])):
        key = (d["actor_id"], d["card_id"], d["action_market_date"])
        first.setdefault(key, d)
    selected = [d for d in first.values() if start <= d["server_time"] < end]
    morning = [
        d
        for d in selected
        if window["start"]
        <= instant(d["server_time"]).astimezone(ZoneInfo(window["timezone"])).strftime("%H:%M:%S.%f")[:5]
        < window["end"]
    ]
    return dict(
        morning_count=len(morning),
        total_first_decisions=len(selected),
        morning_decision_share=text_decimal(Decimal(len(morning)) / len(selected)) if selected else None,
        reason_code=None if selected else "NO_SAMPLES",
        unit="RATIO",
        window=window,
        timezone=window["timezone"],
        config_version=config_version,
        selection_hash=digest(sorted(d["id"] for d in selected)),
    )


def rolling_statistics(records, window="ALL"):
    groups = defaultdict(list)
    for r in records:
        key = tuple(
            r.get(x)
            for x in (
                "dataset",
                "account_id",
                "origin",
                "rule_id",
                "rule_version",
                "experiment_version",
                "costs_version",
                "data_mode",
                "horizon",
                "direction",
            )
        )
        groups[key].append(r)
    result = []
    for key, rs in sorted(groups.items(), key=lambda x: str(x[0])):
        distinct = {}
        for r in rs:
            distinct.setdefault(r.get("economic_event_id") or r.get("episode_id"), r)
        allrows = list(distinct.values())
        pending = sum(r["status"] == "PENDING" for r in allrows)
        unresolved = sum(r["status"] == "UNRESOLVED" for r in allrows)
        completed = [r for r in allrows if r["status"] == "COMPLETED" and r.get("net_pnl") is not None]
        completed.sort(key=lambda x: x.get("economic_event_id") or x.get("episode_id"))
        completed.sort(key=lambda x: x.get("confirmed_at") or x.get("closed_at") or "", reverse=True)
        selected = completed if window == "ALL" else completed[: int(window)]
        vals = [dec(r["net_pnl"]) for r in selected]
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v < 0]
        mean = lambda a: sum(a, Decimal(0)) / len(a) if a else None
        result.append(
            dict(
                group=dict(
                    zip(
                        (
                            "dataset",
                            "account_id",
                            "origin",
                            "rule_id",
                            "rule_version",
                            "experiment_version",
                            "costs_version",
                            "data_mode",
                            "horizon",
                            "direction",
                        ),
                        key,
                    )
                ),
                target_window=window,
                sample_size=len(vals),
                eligible_completed=len(completed),
                pending=pending,
                unresolved=unresolved,
                excluded_by_reason=dict(SKIPPED=sum(r["status"] == "SKIPPED" for r in allrows)),
                selected_ids=[r.get("economic_event_id") or r.get("episode_id") for r in selected],
                selection_hash=digest(selected),
                wins=len(wins),
                losses=len(losses),
                flat=len(vals) - len(wins) - len(losses),
                win_rate=text_decimal(Decimal(len(wins)) / len(vals)) if vals else None,
                mean=text_decimal(mean(vals)) if vals else None,
                median=text_decimal(median(vals)) if vals else None,
                average_win=text_decimal(mean(wins)) if wins else None,
                average_loss=text_decimal(mean(losses)) if losses else None,
                payoff_ratio=text_decimal(mean(wins) / abs(mean(losses))) if wins and losses else None,
                cumulative_closed_pnl=text_decimal(sum(vals, Decimal(0))),
                reason_code=None if vals else "NO_SAMPLES",
                curve_label="入选平仓累计净损益；不等于完整账户最大回撤",
            )
        )
    return result

from src.services.signal_scanner.core import digest, now, stamp, uid, require
from src.services.signal_scanner.domain.risk import DEFAULT_POLICY, validate_policy
from src.services.signal_scanner.domain.rules import RULESET
from src.services.signal_scanner.storage.db import insert, one, audit

DEFAULTS = {
    "risk": ("RISK", DEFAULT_POLICY),
    "rules": ("RULESET", dict(rules=RULESET)),
    "universe": ("UNIVERSE", dict(members=[])),
    "provider": (
        "PROVIDER",
        dict(mode="DISABLED", file="market.json", capabilities=[], feed=None, quote_enabled=False),
    ),
    "events": ("PROVIDER", dict(mode="DISABLED", file="events.json", coverage="UNKNOWN", verified_at=None)),
    "preferences": (
        "USER_PREFERENCE",
        dict(
            display_timezone="Asia/Singapore",
            digest_time="08:00",
            morning_window=dict(start="07:30", end="09:30", timezone="Asia/Singapore"),
            paused=False,
            external_channel="NONE",
        ),
    ),
    "system": (
        "SYSTEM",
        dict(
            account_sync="DISABLED",
            order_execution="FORBIDDEN",
            ibkr_adapter="DEFERRED",
            market_schedule_profile="XNYS_RTH_ONLY",
        ),
    ),
    "experiment": (
        "PAPER_EXPERIMENT",
        dict(
            stage="RESEARCH",
            initial_cash="100000",
            slippage_bps="5",
            exit_sessions=20,
            data_mode="REVISED_HISTORY",
            horizons=[5, 10, 20],
            costs_version="costs-v1",
            rules_version="rules-v1",
        ),
    ),
}


def get_config(cx, key, at=None):
    result = one(
        cx,
        "SELECT * FROM config_versions WHERE config_key=? AND effective_from<=? AND (effective_until IS NULL OR effective_until>?) ORDER BY effective_from DESC LIMIT 1",
        (key, at or stamp(), at or stamp()),
    )
    require(result, "RESOURCE_NOT_FOUND", status=404)
    return dict(
        key=key,
        version=result["version"],
        effective_from=result["effective_from"],
        value=result["payload_json"],
    )


def initialize(cx):
    for key, (kind, value) in DEFAULTS.items():
        if not one(cx, "SELECT id FROM config_versions WHERE config_key=?", (key,)):
            insert(
                cx,
                "config_versions",
                dict(
                    id=uid("cfg"),
                    config_type=kind,
                    config_key=key,
                    version=key + "-v1",
                    effective_from="1990-01-01T00:00:00.000Z",
                    payload_json=value,
                    content_hash=digest(value),
                    change_reason="TDD default, no real data assumed",
                    actor="SYSTEM",
                ),
            )


def put_config(cx, key, payload, cal, artifacts):
    from src.services.signal_scanner.core import reject_credentials

    reject_credentials(payload.value)
    current = get_config(cx, key)
    require(payload.expected_version == current["version"], "REVISION_CONFLICT")
    require(key in DEFAULTS and key != "system", "OUT_OF_RANGE", "系统数据/交易政策不可改为启用", 422)
    value = {**current["value"], **payload.value}
    activation = "IMMEDIATE"
    at = stamp()
    require(
        not any(x.lower() in ("password", "api_key", "secret", "token", "private_key") for x in value),
        "VALIDATION_ERROR",
        "秘密仅通过环境配置",
        422,
    )
    require(not set(value) - set(DEFAULTS[key][1]), "VALIDATION_ERROR", "未知配置字段", 422)
    if key == "risk":
        value = validate_policy(value)
        from src.services.signal_scanner.core import dec

        if any(
            dec(value[x]) > dec(current["value"][x])
            for x in (
                "risk_pct",
                "daily_loss_limit",
                "monthly_loss_limit",
                "portfolio_heat_limit",
                "symbol_notional_limit",
                "sector_notional_limit",
            )
        ):
            at = cal.session(cal.action_date(now()))["open_at"]
            activation = "NEXT_SESSION"
            if at <= stamp():
                at = cal.session(cal.shift(cal.action_date(now()), 1))["open_at"]
    elif key == "rules":
        require(set(value["rules"]) == set(RULESET), "VALIDATION_ERROR", status=422)
        for r, spec in value["rules"].items():
            require(
                spec.get("stage") in ("RESEARCH", "SHADOW", "APPROVED", "DISABLED"),
                "VALIDATION_ERROR",
                status=422,
            )
            require(set(spec) <= set(RULESET[r]) | {"validation_report_ref"}, "VALIDATION_ERROR", status=422)
            if spec["stage"] == "APPROVED" and current["value"]["rules"][r]["stage"] != "APPROVED":
                ref = spec.get("validation_report_ref")
                require(ref, "DATA_INCOMPLETE", "批准需要验证报告", 422)
                report = artifacts.read(ref)
                require(
                    report.get("approval") == "APPROVED"
                    and report.get("rule_id") == r
                    and report.get("rule_version") == spec["version"]
                    and report.get("engineering_passed") is True,
                    "DATA_INCOMPLETE",
                    "报告与规则不匹配",
                    422,
                )
        at = cal.session(cal.shift(cal.action_date(now()), 1))["open_at"]
        activation = "NEXT_SESSION"
    elif key == "universe":
        require(
            isinstance(value["members"], list) and len(value["members"]) <= 300, "OUT_OF_RANGE", status=422
        )
        require(len(set(value["members"])) == len(value["members"]), "VALIDATION_ERROR", status=422)
        for i in value["members"]:
            require(
                one(cx, "SELECT id FROM instruments WHERE id=?", (i,)), "DATA_INCOMPLETE", "证券尚未导入", 422
            )
    elif key == "provider":
        require(
            value["mode"] in ("DISABLED", "LOCAL_FILE", "ALPACA_RTH", "TIINGO_EOD_CACHE"),
            "VALIDATION_ERROR",
            status=422,
        )
        require(
            value["quote_enabled"] is False, "OUT_OF_RANGE", "当前版本仅支持EOD准备，报价能力保持关闭", 422
        )
        require(
            isinstance(value["file"], str) and "/" not in value["file"] and "\\" not in value["file"],
            "VALIDATION_ERROR",
            status=422,
        )
        for c in value["capabilities"]:
            require(
                set(c)
                >= {"capability_id", "support", "entitlement", "enabled", "verified_at", "evidence_ref"},
                "VALIDATION_ERROR",
                status=422,
            )
            require(
                c["support"] in ("SUPPORTED", "UNSUPPORTED", "UNKNOWN")
                and c["entitlement"] in ("GRANTED", "DENIED", "UNKNOWN")
                and isinstance(c["enabled"], bool),
                "VALIDATION_ERROR",
                status=422,
            )
    elif key == "events":
        require(
            value["mode"] in ("DISABLED", "LOCAL_FILE") and value["coverage"] in ("OK", "PARTIAL", "UNKNOWN"),
            "VALIDATION_ERROR",
            status=422,
        )
        require("/" not in value["file"] and "\\" not in value["file"], "VALIDATION_ERROR", status=422)
    elif key == "preferences":
        from zoneinfo import ZoneInfo
        import re

        try:
            ZoneInfo(value["display_timezone"])
            ZoneInfo(value["morning_window"]["timezone"])
        except (KeyError, ValueError):
            require(False, "VALIDATION_ERROR", status=422)
        win = value["morning_window"]
        require(
            all(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", win[x]) for x in ("start", "end"))
            and win["start"] < win["end"],
            "OUT_OF_RANGE",
            status=422,
        )
        require(value["external_channel"] in ("NONE", "WEBHOOK"), "OUT_OF_RANGE", status=422)
    elif key == "experiment":
        require(
            value["exit_sessions"] == 20
            and value["horizons"] == [5, 10, 20]
            and value["data_mode"] in ("POINT_IN_TIME", "REVISED_HISTORY"),
            "OUT_OF_RANGE",
            status=422,
        )
        from src.services.signal_scanner.core import dec

        require(
            dec(value["initial_cash"]) > 0
            and 0 <= dec(value["slippage_bps"]) <= 1000
            and value["stage"] in ("RESEARCH", "SHADOW"),
            "OUT_OF_RANGE",
            status=422,
        )
    # A pending configuration must be explicitly reviewed, never silently replaced.
    require(
        not one(cx, "SELECT id FROM config_versions WHERE config_key=? AND effective_from>?", (key, stamp())),
        "REVISION_CONFLICT",
        "已有待生效版本",
    )
    count = one(cx, "SELECT COUNT(*) AS n FROM config_versions WHERE config_key=?", (key,))["n"]
    version = f"{key}-v{count + 1}"
    cx.execute(
        "UPDATE config_versions SET effective_until=? WHERE config_key=? AND version=?",
        (at, key, current["version"]),
    )
    insert(
        cx,
        "config_versions",
        dict(
            id=uid("cfg"),
            config_type=DEFAULTS[key][0],
            config_key=key,
            version=version,
            effective_from=at,
            payload_json=value,
            content_hash=digest(value),
            change_reason=payload.change_reason,
            actor="owner",
        ),
    )
    audit(cx, "CONFIG_CHANGED", key, payload.change_reason, version=version, activation=activation)
    return dict(version=version, activation=activation, existing_locks_preserved=True)

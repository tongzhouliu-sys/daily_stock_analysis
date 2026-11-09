from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from src.services.signal_scanner.core import canonical, digest, instant, reject_credentials, require
from src.services.signal_scanner.research.versions import verify_versions

REQUIRED = (
    "schema_version",
    "experiment_id",
    "revision",
    "logical_key",
    "actor",
    "ruleset_version",
    "universe_version",
    "calendar_version",
    "data_snapshot_ref",
    "start_date",
    "end_date",
    "warmup_bars",
    "evaluation_clock",
    "cost_assumption",
    "price_basis",
    "benchmark_ref",
    "research_mode",
    "label_as_of",
    "entry_bases",
    "split_policy",
    "min_completed_samples",
    "algorithm_version",
    "engine_version",
    "code_hash",
    "dependency_hash",
    "registration",
    "parameter_overrides",
)


def validate(manifest, cal):
    require(isinstance(manifest, dict), "INVALID_MANIFEST", status=422)
    missing = [k for k in REQUIRED if k not in manifest]
    require(not missing, "MISSING_REQUIRED_INPUT", status=422, fields=missing)
    require(not set(manifest) - set(REQUIRED), "UNKNOWN_MANIFEST_FIELD", status=422)
    m = deepcopy(manifest)
    for key in (
        "evaluation_clock",
        "cost_assumption",
        "price_basis",
        "benchmark_ref",
        "split_policy",
        "registration",
        "parameter_overrides",
    ):
        require(isinstance(m[key], dict), "INVALID_INPUT_TYPE", status=422, field=key)
    require(isinstance(m["entry_bases"], list), "INVALID_ENTRY_BASIS", status=422)
    reject_credentials(m)
    require(m["schema_version"] == 1, "UNSUPPORTED_SCHEMA", status=422)
    for key in ("experiment_id", "logical_key", "actor", "universe_version"):
        require(isinstance(m[key], str) and 0 < len(m[key]) <= 128, "INVALID_IDENTITY", status=422)
    require(isinstance(m["revision"], int) and m["revision"] > 0, "INVALID_REVISION", status=422)
    require(m["research_mode"] in ("POINT_IN_TIME", "REVISED_HISTORY"), "INVALID_RESEARCH_MODE", status=422)
    try:
        start, end = date.fromisoformat(m["start_date"]), date.fromisoformat(m["end_date"])
    except (TypeError, ValueError):
        require(False, "INVALID_DATE_RANGE", status=422)
    require(
        start <= end and cal.session(str(start)) and cal.session(str(end)), "INVALID_DATE_RANGE", status=422
    )
    require(isinstance(m["warmup_bars"], int) and m["warmup_bars"] >= 260, "INVALID_WARMUP", status=422)
    require(
        isinstance(m["min_completed_samples"], int) and m["min_completed_samples"] > 0,
        "INVALID_SAMPLE_THRESHOLD",
        status=422,
    )
    clock = m["evaluation_clock"]
    require(
        isinstance(clock, dict)
        and clock.get("kind") == "SESSION_CLOSE_PLUS_MINUTES"
        and isinstance(clock.get("minutes"), int)
        and 0 <= clock["minutes"] <= 1440,
        "INVALID_EVALUATION_CLOCK",
        status=422,
    )
    instant(m["label_as_of"])
    require(
        m["entry_bases"]
        and len(set(m["entry_bases"])) == len(m["entry_bases"])
        and set(m["entry_bases"]) <= {"NEXT_AVAILABLE_OPEN", "CONFIRM_CLOSE"},
        "INVALID_ENTRY_BASIS",
        status=422,
    )
    require(
        m["price_basis"].get("kind") == "ADJUSTED" and m["price_basis"].get("adjustment_epoch"),
        "INVALID_PRICE_BASIS",
        status=422,
    )
    require(
        m["price_basis"].get("return_kind") in ("PRICE_RETURN", "TOTAL_RETURN"),
        "INVALID_RETURN_KIND",
        status=422,
    )
    cost = m["cost_assumption"]
    require(
        cost.get("version") and cost.get("currency") == "USD" and cost.get("slippage_bps") == [5, 10, 20],
        "INVALID_COST_POLICY",
        status=422,
    )
    require(cost.get("fee_unit") in ("BPS_OF_ENTRY", "FIXED_USD"), "INVALID_FEE_UNIT", status=422)
    for k in ("entry_fee", "exit_fee"):
        value = cost.get(k)
        require(isinstance(value, str), "MISSING_FEE", status=422)
        try:
            d = Decimal(value)
        except (InvalidOperation, ValueError):
            require(False, "INVALID_FEE", status=422)
        require(d.is_finite() and d >= 0, "INVALID_FEE", status=422)
    if cost["fee_unit"] == "FIXED_USD":
        try:
            notional = Decimal(str(cost.get("reference_notional", 0)))
        except (InvalidOperation, ValueError):
            require(False, "MISSING_REFERENCE_NOTIONAL", status=422)
        require(notional.is_finite() and notional > 0, "MISSING_REFERENCE_NOTIONAL", status=422)
    require(
        m["split_policy"].get("kind") in ("EXPLORATORY", "CHRONOLOGICAL_60_20_20"),
        "INVALID_SPLIT_POLICY",
        status=422,
    )
    if m["split_policy"]["kind"] == "CHRONOLOGICAL_60_20_20":
        require((end - start).days >= 365 * 5, "INSUFFICIENT_FIVE_YEAR_RANGE", status=422)
        require(m["split_policy"].get("embargo_sessions") == 20, "INVALID_EMBARGO", status=422)
    else:
        require((end - start).days < 365 * 5, "FIVE_YEAR_SPLIT_REQUIRED", status=422)
    require(
        m["benchmark_ref"].get("market") and m["benchmark_ref"].get("version"),
        "MISSING_BENCHMARK",
        status=422,
    )
    reg = m["registration"]
    require(
        reg.get("id") and reg.get("registered_at") and reg.get("hypothesis") and reg.get("sealed_policy"),
        "INVALID_REGISTRATION",
        status=422,
    )
    instant(reg["registered_at"])
    for rule, params in m["parameter_overrides"].items():
        allowed = reg.get("allowed_variations", {})
        require(rule in allowed and set(params) <= set(allowed[rule]), "UNREGISTERED_PARAMETER", status=422)
        from src.services.signal_scanner.domain.rule_parameters import DEFAULT_PARAMETERS

        for key, value in params.items():
            baseline = DEFAULT_PARAMETERS.get(rule, {}).get(key)
            require(
                baseline is not None
                and value in allowed[rule][key]
                and any(abs(float(value) - baseline * factor) < 1e-10 for factor in (0.8, 1, 1.2)),
                "INVALID_SENSITIVITY",
                status=422,
            )
    verify_versions(m, cal)
    # Ensures no NaN/Infinity can enter a business hash.
    canonical(m)
    return m


def semantic_manifest(m):
    value = {k: v for k, v in m.items() if k not in ("actor", "logical_key", "experiment_id", "revision")}
    value["data_snapshot_ref"] = {"sha256": m["data_snapshot_ref"]["sha256"]}
    return value


def input_hash(m, snapshot):
    return digest(dict(manifest=semantic_manifest(m), snapshot=snapshot))

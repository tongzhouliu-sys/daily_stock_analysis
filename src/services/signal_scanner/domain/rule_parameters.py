"""Versioned defaults shared by production rules and registered experiments."""

from copy import deepcopy

from src.services.signal_scanner.core import require

DEFAULT_PARAMETERS = {
    "TR-01": dict(ttl=3, wait=3, atr_buffer=0.5, rv_min=1.5, adx_min=25),
    "TR-02": dict(ttl=3, wait=3, adx_min=25, confirm_rv=1),
    "TR-03": dict(ttl=3, wait=2, parent_sessions=10, atr_buffer=0.5, rv_max=0.8, clv_min=0.6),
    "VP-01": dict(ttl=3, wait=3, atr_buffer=0.5, rv_min=2, adx_min=25, clv_min=0.7),
    "VP-02": dict(ttl=3, wait=2, rv_max=1, atr_buffer=0.5, recover_rv=1.5),
    "VP-03": dict(ttl=3, wait=1, return_min=0.1, rv_min=2, body_max=0.4, clv_max=0.6, recover_rv=1.5),
    "VP-04": dict(ttl=1, wait=1, range_atr=1.5, body_min=0.6, rv_max=0.8),
    "VP-05": dict(ttl=3, wait=3),
    "MO-01": dict(ttl=2, wait=2, adx_max=20, rsi_level=30, confirm_rv=1),
    "MO-02": dict(ttl=3, wait=3),
    "EV-01": dict(ttl=3, wait=3, gap_min=0.03, rv_min=2),
    "RK-01": dict(ttl=2, wait=2, shock_multiple=2),
}


def parameters_for(rule, overrides=None):
    base = DEFAULT_PARAMETERS[rule]
    require(not set(overrides or {}) - set(base), "UNKNOWN_RULE_PARAMETER", status=422)
    result = {**base, **(overrides or {})}
    for k, v in result.items():
        require(
            isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 10000,
            "INVALID_RULE_PARAMETER",
            status=422,
        )
        if k in ("ttl", "wait", "parent_sessions"):
            require(isinstance(v, int), "INVALID_SESSION_PARAMETER", status=422)
    return result


def defaults():
    return deepcopy(DEFAULT_PARAMETERS)

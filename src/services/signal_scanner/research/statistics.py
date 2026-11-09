from collections import Counter
from decimal import Decimal

from src.services.signal_scanner.core import canonical, text_decimal


def quantile(values, p):
    if not values:
        return None
    a = sorted(Decimal(str(x)) for x in values)
    h = Decimal(len(a) - 1) * Decimal(str(p))
    i = int(h)
    return a[i] if i == len(a) - 1 else a[i] + (a[i + 1] - a[i]) * (h - i)


class Accumulator:
    def __init__(self):
        self.counts = Counter()
        self.reasons = Counter()
        self.gross = []
        self.net = []

    def add(self, r):
        self.counts[r["status"]] += 1
        if r.get("reason_code") or r.get("excluded_reason"):
            self.reasons[r.get("reason_code") or r["excluded_reason"]] += 1
        if r["status"] == "COMPLETED":
            self.gross.append(Decimal(r["gross_return"]))
            self.net.append(Decimal(r["net_return"]))

    def result(self, minimum):
        n = self.counts["COMPLETED"]

        def summary(a):
            return dict(
                mean=text_decimal(sum(a) / len(a)) if a else None,
                **{
                    f"p{int(p * 100):02}": text_decimal(quantile(a, p)) if a else None
                    for p in (0.05, 0.25, 0.50, 0.75, 0.95)
                },
            )

        return dict(
            status="NO_SAMPLES"
            if not sum(self.counts.values())
            else "SUFFICIENT"
            if n >= minimum
            else "INSUFFICIENT",
            eligible_confirmed=sum(self.counts.values()),
            completed=n,
            pending=self.counts["PENDING"],
            unresolved=self.counts["UNRESOLVED"],
            excluded=self.counts["EXCLUDED"],
            exclusion_reasons=dict(sorted(self.reasons.items())),
            gross=summary(self.gross),
            net=summary(self.net),
            quantile_method="LINEAR_N_MINUS_ONE",
            min_completed_samples=minimum,
        )


def summarize(rows, minimum, *, unconditional=False):
    groups = {}
    for row in rows:
        strong = {
            k: row.get(k)
            for k in ("research_mode", "horizon", "entry_basis", "cost_tier", "split", "direction")
        }
        dims = [("ECONOMIC_TOTAL", None, None)]
        if unconditional:
            dims = [("INSTRUMENT", row["instrument_id"], None)]
        else:
            dims += [("RULE", r["rule_id"], r["rule_version"]) for r in row["rule_refs"]]
            dims += [("YEAR", row["confirmation_market_date"][:4], None)]
            dims += [
                (k.upper(), row.get(k, "UNKNOWN"), None) for k in ("environment", "sector", "asset_type")
            ]
        for dimension, value, version in dims:
            key = {**strong, "dimension": dimension, "value": value, "rule_version": version}
            encoded = canonical(key)
            if encoded not in groups:
                groups[encoded] = (key, Accumulator())
            groups[encoded][1].add(row)
    return [{**key, **acc.result(minimum)} for _, (key, acc) in sorted(groups.items())]


def lifecycle_counts(events, assessments):
    result = []
    from src.services.signal_scanner.domain.rules import RULES

    for rule in RULES:
        es = [e for e in events if e["rule_id"] == rule]
        rule_assess = [r for r in assessments if r["rule_id"] == rule]
        evaluated = sum(r.get("count", 1) for r in rule_assess if r["status"] == "EVALUATED")
        result.append(
            dict(
                rule_id=rule,
                triggered=len(es),
                confirmed=sum(bool(e.get("confirmed_at")) for e in es),
                invalidated=sum(e["lifecycle"] == "INVALIDATED" for e in es),
                expired=sum(e["lifecycle"] == "EXPIRED" for e in es),
                blocked=sum(bool(e.get("blocked_reasons")) for e in es),
                evaluable_security_sessions=evaluated,
                not_evaluable=sum(r.get("count", 1) for r in rule_assess) - evaluated,
                triggers_per_1000_sessions=text_decimal(Decimal(len(es)) * 1000 / evaluated)
                if evaluated
                else None,
            )
        )
    return result

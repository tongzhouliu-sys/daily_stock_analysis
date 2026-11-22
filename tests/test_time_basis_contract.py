# -*- coding: utf-8 -*-
"""Contract for naive datetime semantics in src/storage.py.

The database has two deliberate conventions:

- ``local``: naive server-local time (``datetime.now``), used by legacy tables
  whose API layer serializes with ``value.astimezone()``.
- ``utc``: naive UTC time (``utc_naive_now``), used by newer tables whose API
  layer serializes with ``to_utc_isoformat``.

Adding a table or column without choosing a convention is how off-by-hours bugs
sneak in, so every model with a DateTime default must appear here.
"""

from __future__ import annotations

import ast
from pathlib import Path

STORAGE = Path(__file__).resolve().parents[1] / "src" / "storage.py"

LOCAL_TIME_TABLES = {
    "DatabaseSchemaMigration",
    "StockDaily",
    "NewsIntel",
    "IntelligenceSource",
    "IntelligenceItem",
    "FundamentalSnapshot",
    "AnalysisHistory",
    "ReportTextVariant",
    "BacktestResult",
    "BacktestSummary",
    "PortfolioAccount",
    "PortfolioTrade",
    "PortfolioCashLedger",
    "PortfolioCorporateAction",
    "PortfolioPosition",
    "PortfolioPositionLot",
    "PortfolioDailySnapshot",
    "PortfolioFxRate",
    "ConversationMessage",
    "ConversationSessionState",
    "ConversationSessionPin",
    "IntakeDraftRecord",
    "ConversationSummary",
    "AgentProviderTurn",
    "LLMUsage",
    "AlertRuleRecord",
    "AlertTriggerRecord",
    "AlertNotificationRecord",
    "AlertCooldownRecord",
    "AlertRuleStateRecord",
}

UTC_TIME_TABLES = {
    "ScreeningRun",
    "DecisionSignalRecord",
    "DecisionSignalOutcomeRecord",
    "DecisionSignalFeedbackRecord",
    "SkillOpinionSampleRecord",
    "SkillOpinionOutcomeRecord",
    "LoginAuditLog",
    "PredictionMarketEvent",
}


def _datetime_defaults() -> list[tuple[str, str, str]]:
    tree = ast.parse(STORAGE.read_text(encoding="utf-8"))
    found: list[tuple[str, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.Assign) or not isinstance(item.value, ast.Call):
                continue
            func = item.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "Column":
                continue
            source = ast.unparse(item.value)
            if "DateTime" not in source or "default=" not in source:
                continue
            default = source.split("default=", 1)[1].split(",", 1)[0].split(")", 1)[0]
            column = ast.unparse(item.targets[0])
            found.append((node.name, column, default))
    return found


def test_every_datetime_default_declares_a_known_basis() -> None:
    rows = _datetime_defaults()
    assert rows, "expected DateTime defaults in src/storage.py"

    problems: list[str] = []
    for table, column, default in rows:
        if table in LOCAL_TIME_TABLES and default.endswith("datetime.now"):
            continue
        if table in UTC_TIME_TABLES and default.endswith("utc_naive_now"):
            continue
        problems.append(f"{table}.{column} default={default}")

    assert not problems, (
        "Add the table to LOCAL_TIME_TABLES or UTC_TIME_TABLES in "
        "tests/test_time_basis_contract.py and document the choice in "
        "docs/ai/DATA-FLOW.md. Unclassified columns:\n" + "\n".join(problems)
    )


def test_basis_sets_do_not_overlap() -> None:
    assert not (LOCAL_TIME_TABLES & UTC_TIME_TABLES)

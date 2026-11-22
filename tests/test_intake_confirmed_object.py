"""The confirmed intake object must bind the first chat turn (no second card)."""

from __future__ import annotations

import json

import pytest

from src.config import Config
from src.storage import DatabaseManager


@pytest.fixture(autouse=True)
def isolated_database_manager():
    DatabaseManager.reset_instance()
    Config.reset_instance()
    yield
    DatabaseManager.reset_instance()
    Config.reset_instance()


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(db_url=f"sqlite:///{tmp_path / 'chat.db'}")


@pytest.fixture
def chat_stock_index(tmp_path, monkeypatch):
    from src.data import stock_index_loader

    def row(code, display, name, market="CN", kind="stock", aliases=None):
        return [code, display, name, "fixture", "fx", aliases or [], market, kind, True, 1]

    payload = [
        row("600519.SH", "600519", "贵州茅台", aliases=["茅台", "SH600519"]),
        row("300750.SZ", "300750", "宁德时代"),
    ]
    path = tmp_path / "stocks.index.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stock_index_loader.clear_stock_index_cache()
    monkeypatch.setattr(stock_index_loader, "get_stock_index_candidate_paths", lambda: (path,))
    monkeypatch.setattr(stock_index_loader, "get_remote_stock_index_cache_path", lambda: tmp_path / "absent.json")
    yield path
    stock_index_loader.clear_stock_index_cache()


def test_confirmed_intake_object_skips_the_clarification_turn(db, chat_stock_index):
    from src.services.agent_chat_session_service import AgentChatSessionService

    service = AgentChatSessionService(db_manager=db)
    resolved = service.prepare_session_turn(
        Config(),
        "session-a",
        "换一只帮我看看",
        None,
        context={},
        confirmed_object={"stock_code": "600519", "canonical_id": "600519.SH"},
    )
    assert resolved.clarification is None
    assert resolved.active_stock_context is not None
    assert resolved.active_stock_context["stock_code"] == "600519"
    assert resolved.confirm_stock is True


def test_without_confirmed_object_no_guess_is_bound(db, chat_stock_index):
    """Legacy path: a message without any object must not invent one."""
    from src.services.agent_chat_session_service import AgentChatSessionService

    service = AgentChatSessionService(db_manager=db)
    resolved = service.prepare_session_turn(
        Config(), "session-b", "换一只帮我看看", None, context={},
    )
    assert resolved.active_stock_context is None
    assert resolved.clarification is None


def test_confirmed_object_does_not_override_an_explicit_switch(db, chat_stock_index):
    """A later turn must keep the R9 switch path even with a stale brief object."""
    from src.services.agent_chat_session_service import AgentChatSessionService

    service = AgentChatSessionService(db_manager=db)
    first = service.prepare_session_turn(
        Config(), "session-c", "分析 600519", None, context={},
    )
    assert first.active_stock_context["stock_code"] == "600519"
    second = service.prepare_session_turn(
        Config(),
        "session-c",
        "宁德时代怎么样",
        None,
        context={},
        confirmed_object={"stock_code": "600519", "canonical_id": "600519.SH"},
    )
    assert second.clarification is None
    assert second.active_stock_context["stock_code"] == "300750"

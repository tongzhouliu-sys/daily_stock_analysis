# -*- coding: utf-8 -*-
"""T27: 空偏好跳过回答编辑。

引导偏好全部为空 / ``unknown`` 时，回答编辑器没有可对齐的目标，
多跑一次整篇改写只是白花一次模型调用。这里锁定该判定与两条执行路径的行为。
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock, patch

from src.agent.answer_editor import brief_has_preferences
from src.schemas.agent_intake import AnalysisBrief, ObjectIdentity
from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestBriefHasPreferences(unittest.TestCase):
    def test_none_and_empty_brief(self) -> None:
        self.assertFalse(brief_has_preferences(None))
        self.assertFalse(brief_has_preferences(AnalysisBrief()))
        self.assertFalse(brief_has_preferences({}))

    def test_unknown_sentinels_do_not_count(self) -> None:
        brief = AnalysisBrief(
            goal="unknown",
            horizon=" UNKNOWN ",
            position="n/a",
            risk_focus="-",
            reading_style="",
            focus_areas=[],
            unknown_fields=["goal", "horizon", "position"],
        )
        self.assertFalse(brief_has_preferences(brief))

    def test_any_real_preference_counts(self) -> None:
        for brief in (
            AnalysisBrief(goal="波段"),
            AnalysisBrief(horizon="3个月"),
            AnalysisBrief(position="半仓"),
            AnalysisBrief(risk_focus="回撤控制"),
            AnalysisBrief(reading_style="结论先行"),
            AnalysisBrief(focus_areas=["量能"]),
            AnalysisBrief(object=ObjectIdentity(
                stock_code="600519", stock_name="贵州茅台",
                canonical_id="600519.SH", asset_type="stock",
                market="CN", market_label="A股",
            )),
            AnalysisBrief(object_scope="600519"),
        ):
            self.assertTrue(brief_has_preferences(brief), brief)

    def test_serialized_dict_brief(self) -> None:
        self.assertFalse(brief_has_preferences({"goal": None, "focus_areas": []}))
        self.assertTrue(brief_has_preferences({"goal": "趋势追踪"}))


class TestEditorSkippedWhenNoPreferences(unittest.TestCase):
    def _run_executor(self, brief, editor_mock):
        from src.agent.chat_executor import AgentChatExecutor

        executor = AgentChatExecutor.__new__(AgentChatExecutor)
        executor.max_steps = 5
        executor.timeout_seconds = None
        executor.config = SimpleNamespace()
        executor.context_llm_adapter = None

        turn = SimpleNamespace(
            session_id="brief-session",
            message="hi",
            run_id="run-brief",
            baseline_len=0,
            user_message_id=None,
            accepted_snapshot=None,
            db_manager=None,
            prepared=SimpleNamespace(
                system_prompt="sys",
                history_messages=[],
                stock_scope=None,
                analysis_brief=brief,
                report_language="zh",
            ),
        )
        executor.backend = MagicMock()
        executor.backend.run.return_value = SimpleNamespace(
            success=True,
            final_answer="answer",
            tool_calls_log=[],
            total_steps=1,
            model="",
            backend="litellm",
            usage=None,
            diagnostics={},
            error_message=None,
            error_code=None,
            messages=[],
            cancelled=False,
        )

        with patch("src.agent.chat_executor.conversation_manager") as manager, \
             patch("src.agent.answer_editor.StockAnswerEditorService") as editor_cls:
            manager.add_message.return_value = 7
            manager.set_running.return_value = True
            editor_cls.return_value = editor_mock
            executor.execute_turn(turn)
        return editor_cls

    def test_editor_not_called_for_empty_brief(self) -> None:
        editor_mock = MagicMock()
        editor_mock.format_response_sync.return_value = ("edited", None)
        editor_cls = self._run_executor(AnalysisBrief(), editor_mock)
        editor_cls.assert_not_called()
        editor_mock.format_response_sync.assert_not_called()

    def test_editor_called_for_non_empty_brief(self) -> None:
        editor_mock = MagicMock()
        editor_mock.format_response_sync.return_value = ("edited", None)
        editor_cls = self._run_executor(AnalysisBrief(goal="波段"), editor_mock)
        editor_cls.assert_called_once()
        editor_mock.format_response_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()

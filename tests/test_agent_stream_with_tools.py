# -*- coding: utf-8 -*-
"""T25: 问股正文流式输出。

覆盖：
- 带工具声明时仍然走流式（此前 ``not tools`` 让正文从不流式）
- 正文增量实时回调，工具调用增量按 index 拼装成与非流式一致的 ToolCall
- 正文后出现工具调用时先发 reset 通知
- 收到任何正文片段之前失败时回退为非流式重试一次
- runner 只通过 content_callback 下发正文，并发出阶段事件
"""

from __future__ import annotations

import sys
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from typing import Any, Dict, List, Optional

ensure_stub = None
from tests.litellm_stub import ensure_litellm_stub  # noqa: E402

ensure_litellm_stub()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _chunk(content: Optional[str] = None, tool_calls: Optional[List[Any]] = None,
           finish_reason: Optional[str] = None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        usage=None,
    )


def _tool_call_delta(index: int, *, tc_id: Optional[str] = None,
                     name: Optional[str] = None,
                     arguments: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.agent_litellm_model = "gpt-4o-mini"
    cfg.litellm_model = None
    cfg.litellm_fallback_models = []
    cfg.llm_model_list = []
    cfg.llm_temperature = 0.7
    cfg.gemini_api_keys = []
    cfg.anthropic_api_keys = []
    cfg.openai_api_keys = []
    cfg.deepseek_api_keys = []
    cfg.openai_base_url = None
    cfg.agent_llm_call_timeout_s = 0.0
    cfg.agent_opt_in_thinking = False
    cfg.llm_prompt_cache_diagnostics_level = "off"
    return cfg


class TestStreamingWithTools(unittest.TestCase):
    def setUp(self) -> None:
        from src.agent.llm_adapter import LLMToolAdapter
        self.adapter = LLMToolAdapter(config=_make_config())

    def test_tools_enable_streaming_and_stream_text(self) -> None:
        deltas: List[str] = []
        response_chunks = [
            _chunk("你好"),
            _chunk("，世界"),
            _chunk(None, finish_reason="stop"),
        ]

        with patch("src.agent.llm_adapter.litellm.completion", return_value=iter(response_chunks)) as mock_completion, \
             patch("src.agent.llm_adapter.stream_chunk_builder", create=True), \
             patch("src.agent.llm_adapter.get_api_keys_for_model", return_value=[]):
            result = self.adapter.call_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "noop"}}],
                stream_callback=deltas.append,
            )

        self.assertEqual(deltas, ["你好", "，世界"])
        self.assertEqual(result.content, "你好，世界")
        self.assertIn("stream", mock_completion.call_args.kwargs)

    def test_tool_call_deltas_are_reassembled(self) -> None:
        deltas: List[str] = []
        resets: List[str] = []
        chunks = [
            _chunk(None, tool_calls=[_tool_call_delta(0, tc_id="call_1", name="get_quote", arguments="{\"code\":")]),
            _chunk(None, tool_calls=[_tool_call_delta(0, arguments="\"600519\"}")]),
            _chunk(None, finish_reason="tool_calls"),
        ]

        with patch("src.agent.llm_adapter.litellm.completion", return_value=iter(chunks)), \
             patch("src.agent.llm_adapter.get_api_keys_for_model", return_value=[]):
            result = self.adapter.call_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "get_quote"}}],
                stream_callback=deltas.append,
                stream_reset_callback=resets.append,
            )

        self.assertEqual(deltas, [])
        self.assertEqual(resets, [])
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].id, "call_1")
        self.assertEqual(result.tool_calls[0].name, "get_quote")
        self.assertEqual(result.tool_calls[0].arguments, {"code": "600519"})

    def test_reset_emitted_when_tool_call_follows_streamed_text(self) -> None:
        deltas: List[str] = []
        resets: List[str] = []
        chunks = [
            _chunk("我先看一下"),
            _chunk(None, tool_calls=[_tool_call_delta(0, tc_id="call_2", name="search", arguments="{}")]),
            _chunk(None, finish_reason="tool_calls"),
        ]

        with patch("src.agent.llm_adapter.litellm.completion", return_value=iter(chunks)), \
             patch("src.agent.llm_adapter.get_api_keys_for_model", return_value=[]):
            result = self.adapter.call_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
                stream_callback=deltas.append,
                stream_reset_callback=resets.append,
            )

        self.assertEqual(deltas, ["我先看一下"])
        self.assertEqual(resets, ["tool_call"])
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "search")

    def test_falls_back_to_non_stream_before_any_delta(self) -> None:
        deltas: List[str] = []
        fallback_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="fallback answer", tool_calls=None),
                finish_reason="stop",
            )],
            usage=None,
        )
        calls: List[Dict[str, Any]] = []

        def fake_completion(**kwargs):
            calls.append(kwargs)
            if kwargs.get("stream"):
                def _boom():
                    raise RuntimeError("stream unavailable")
                    yield  # pragma: no cover
                return _boom()
            return fallback_response

        with patch("src.agent.llm_adapter.litellm.completion", side_effect=fake_completion), \
             patch("src.agent.llm_adapter.get_api_keys_for_model", return_value=[]):
            result = self.adapter.call_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[],
                stream_callback=deltas.append,
            )

        self.assertEqual(deltas, [])
        self.assertEqual(result.content, "fallback answer")
        self.assertTrue(calls[0].get("stream"))
        self.assertNotIn("stream", calls[1])


class TestRunnerSingleContentChannel(unittest.TestCase):
    """runner 只通过 content_callback 下发正文，避免同一片段下发两次。"""

    def test_content_delta_events_not_duplicated_by_runner(self) -> None:
        from src.agent.tools.registry import ToolRegistry
        from src.agent.runner import run_agent_loop
        from src.agent.llm_adapter import LLMResponse

        progress_events: List[Dict[str, Any]] = []
        content_chunks: List[str] = []

        registry = ToolRegistry()
        fake_adapter = MagicMock()
        fake_adapter.call_with_tools.return_value = LLMResponse(
            content="最终答案",
            tool_calls=[],
            provider="openai",
            model="gpt-4o-mini",
        )

        # 模拟适配器在回调里分段推送正文
        def _call_with_tools(_messages, _tools, **kwargs):
            cb = kwargs.get("stream_callback")
            if cb is not None:
                cb("最终")
                cb("答案")
            return fake_adapter.call_with_tools.return_value

        fake_adapter.call_with_tools.side_effect = _call_with_tools

        result = run_agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            tool_registry=registry,
            llm_adapter=fake_adapter,
            max_steps=1,
            progress_callback=progress_events.append,
            content_callback=content_chunks.append,
        )

        self.assertTrue(result.success)
        self.assertEqual(content_chunks, ["最终", "答案"])
        self.assertEqual(
            [e for e in progress_events if e.get("type") == "content_delta"],
            [],
        )
        generating = [e for e in progress_events if e.get("type") == "generating"]
        self.assertEqual(len(generating), 1)
        self.assertIn("正在生成回答", generating[0]["message"])


if __name__ == "__main__":
    unittest.main()

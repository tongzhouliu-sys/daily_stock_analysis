# -*- coding: utf-8 -*-
"""R13: analysis task cancellation must actually stop the work.

Two layers are covered here:

* the task queue, which owns the cancellation event and the terminal state;
* the analysis pipeline, whose progress bridge must let ``TaskCancelledError``
  bubble up instead of logging it as a "progress callback failure" and
  continuing all the way to history persistence and notification.
"""

from __future__ import annotations

import os
import sys
import unittest
from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.analyzer import AnalysisResult
from src.core.task_cancellation import TaskCancelledError
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.services.task_queue import AnalysisTaskQueue, TaskStatus
from tests.support import make_pipeline


def _analysis_result() -> AnalysisResult:
    return AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=62,
        trend_prediction="震荡",
        operation_advice="持有",
        decision_type="hold",
    )


def _make_pipeline():
    pipeline = make_pipeline()
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=False,
        realtime_source_priority=[],
        agent_mode=False,
        agent_skills=[],
        save_context_snapshot=False,
        report_language="zh",
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
    )
    pipeline.source_message = None
    pipeline.query_id = None
    pipeline.query_source = "system"
    pipeline.save_context_snapshot = False
    pipeline.progress_callback = None
    pipeline.analysis_skills = None
    pipeline.analysis_phase = "auto"
    pipeline.social_sentiment_service = None

    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_realtime_quote.return_value = None
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.get_fundamental_context.return_value = {
        "market": "cn",
        "coverage": {"boards": "not_supported"},
        "source_chain": [],
    }
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {
        "market": "cn",
        "coverage": {"boards": "not_supported"},
        "source_chain": [],
    }

    pipeline.db = MagicMock()
    pipeline.db.get_data_range.return_value = []
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "date": "2026-03-26",
        "today": {},
        "yesterday": {},
    }

    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = _analysis_result()
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = False
    pipeline.search_service.news_window_days = 3
    return pipeline


class PipelineCancellationTestCase(unittest.TestCase):
    """A cancelled task must not persist history or emit notifications."""

    def test_progress_callback_cancellation_stops_before_history_and_notification(self) -> None:
        pipeline = _make_pipeline()
        cancel_events = []

        def _progress(progress: int, message: str) -> None:
            # Simulate the task queue's cooperative checkpoint: once the user
            # asks for cancellation every later step raises.
            if progress >= 50:
                raise TaskCancelledError("任务 task-1 已取消")
            cancel_events.append(progress)

        pipeline.progress_callback = _progress

        with patch.object(
            StockAnalysisPipeline,
            "_extract_decision_signal_after_history_save",
        ) as extract_signal, self.assertRaises(TaskCancelledError):
            pipeline.analyze_stock(
                "600519",
                ReportType.SIMPLE,
                "task-1",
                current_time=datetime(2026, 3, 27, 10, 0),
            )

        self.assertTrue(cancel_events, "expected at least one progress checkpoint before cancellation")
        pipeline.db.save_analysis_history.assert_not_called()
        extract_signal.assert_not_called()

    def test_progress_bridge_does_not_swallow_task_cancellation(self) -> None:
        pipeline = _make_pipeline()

        def _progress(progress: int, message: str) -> None:
            raise TaskCancelledError("cancelled")

        pipeline.progress_callback = _progress

        with self.assertRaises(TaskCancelledError):
            pipeline._emit_progress(12, "fetching")


class TaskQueueCancellationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None
        self.addCleanup(self._restore_instance)
        self.queue = AnalysisTaskQueue(max_workers=1)
        self._pending_future = Future()
        self.queue._executor = SimpleNamespace(
            submit=lambda *args, **kwargs: self._pending_future,
        )

    def _restore_instance(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False, cancel_futures=True)
        AnalysisTaskQueue._instance = self._original_instance

    def _submit_task(self) -> str:
        task = self.queue.submit_task(
            stock_code="600519",
            stock_name="贵州茅台",
            report_type="simple",
            force_refresh=False,
        )
        return task.task_id

    def test_cancel_before_start_marks_task_cancelled_without_running_it(self) -> None:
        task_id = self._submit_task()
        cancelled_future = MagicMock()
        cancelled_future.cancel.return_value = True
        self.queue._futures[task_id] = cancelled_future

        snapshot = self.queue.cancel_task(task_id)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.status, TaskStatus.CANCELLED)
        cancelled_future.cancel.assert_called_once()
        self.assertFalse(self._pending_future.running())
        self.assertNotIn(task_id, self.queue._futures)
        # The dedupe slot must be released so the same stock can be analyzed again.
        self.assertEqual(self.queue._analyzing_stocks, {})

    def test_cancel_while_running_aborts_at_the_next_checkpoint(self) -> None:
        task_id = self._submit_task()
        not_cancellable_future = MagicMock()
        not_cancellable_future.cancel.return_value = False
        self.queue._futures[task_id] = not_cancellable_future

        completed_steps = []
        queue = self.queue

        class _Service:
            last_error = None

            def analyze_stock(self, stock_code, **kwargs):
                callback = kwargs["progress_callback"]
                completed_steps.append("before-checkpoint")
                # The user hits "cancel" while the task is running.
                queue.cancel_task(task_id)
                # The queue raises out of the progress bridge once cancellation
                # is requested; the service must not return a usable result.
                callback(30, "正在分析")
                completed_steps.append("after-checkpoint")
                return {"code": stock_code}

        with patch("src.services.analysis_service.AnalysisService", _Service):
            result = self.queue._execute_task(
                task_id,
                "600519",
                "simple",
                False,
            )

        self.assertIsNone(result)
        self.assertEqual(completed_steps, ["before-checkpoint"])
        task = self.queue.get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task.status, TaskStatus.CANCELLED)

    def test_cancelling_an_unknown_task_returns_none(self) -> None:
        self.assertIsNone(self.queue.cancel_task("does-not-exist"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Regression tests for the T35 backend hardening items.

Covered:
- B10: score/trend signal mapping is monotonic (bear alignment no longer
  scores a low signal better than a high one).
- D8: cooperative task cancellation in ``AnalysisTaskQueue`` + screening
  single-flight guard.
- D11: scheduler restart on config save does not kill a running analysis.
- D12: CSV upload size ceiling and session rotation after password change.
"""

from __future__ import annotations

import asyncio
import io
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    sys.modules["litellm"] = MagicMock()

from fastapi import HTTPException

from src.stock_analyzer import (
    BuySignal,
    TrendStatus,
    map_signal_for_score,
    signal_rank,
)


class SignalMappingMonotonicTestCase(unittest.TestCase):
    """B10: higher score must never yield a less bullish signal."""

    def test_rank_is_non_decreasing_in_score_for_every_trend(self) -> None:
        for trend in TrendStatus:
            previous_rank = -1
            for score in range(0, 101):
                rank = signal_rank(map_signal_for_score(score, trend))
                self.assertGreaterEqual(
                    rank,
                    previous_rank,
                    f"score {score} in trend {trend.name} regressed",
                )
                previous_rank = rank

    def test_bear_alignment_high_score_is_not_worse_than_medium_score(self) -> None:
        # 70 -> "buy" band, 50 -> "watch" band. The old branch order mapped
        # "watch" to WAIT but "buy" to STRONG_SELL in bear alignment.
        for trend in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR, TrendStatus.WEAK_BEAR):
            high = map_signal_for_score(70, trend)
            medium = map_signal_for_score(50, trend)
            self.assertGreaterEqual(
                signal_rank(high),
                signal_rank(medium),
                f"{trend.name}: 70 -> {high} worse than 50 -> {medium}",
            )

    def test_trend_caps_score_signal(self) -> None:
        self.assertIs(map_signal_for_score(95, TrendStatus.STRONG_BULL), BuySignal.STRONG_BUY)
        self.assertIs(map_signal_for_score(95, TrendStatus.WEAK_BULL), BuySignal.BUY)
        self.assertIs(map_signal_for_score(95, TrendStatus.CONSOLIDATION), BuySignal.WAIT)
        self.assertIs(map_signal_for_score(95, TrendStatus.BEAR), BuySignal.SELL)
        self.assertIs(map_signal_for_score(95, TrendStatus.STRONG_BEAR), BuySignal.STRONG_SELL)
        self.assertIs(map_signal_for_score(10, TrendStatus.STRONG_BULL), BuySignal.STRONG_SELL)

    def test_unknown_score_defaults_to_wait_band(self) -> None:
        self.assertIs(map_signal_for_score(None, TrendStatus.STRONG_BULL), BuySignal.WAIT)


class ScreeningSingleFlightTestCase(unittest.TestCase):
    """D8: only one screening task may be pending/processing at a time."""

    def setUp(self) -> None:
        from api.v1.endpoints import screening as screening_endpoint
        from src.services.task_queue import TaskInfo, TaskStatus as QueueTaskStatus

        self.endpoint = screening_endpoint
        self.TaskInfo = TaskInfo
        self.QueueTaskStatus = QueueTaskStatus
        self.request = SimpleNamespace(
            strategy="dual_low",
            market="cn",
            max_results=5,
            variant_seed=None,
        )

    def _task(self, task_id: str, status):
        return self.TaskInfo(
            task_id=task_id,
            stock_code="screening_screen",
            report_type=self.endpoint.SCREENING_TASK_REPORT_TYPE,
            status=status,
        )

    def _call(self, queue):
        return self.endpoint.screening_start_screen_task(
            request=self.request,
            http_request=MagicMock(),
            config=MagicMock(),
            db_manager=MagicMock(),
        )

    def test_rejects_second_screening_submission(self) -> None:
        queue = MagicMock()
        queue.list_pending_tasks.return_value = [
            self._task("active-screen", self.QueueTaskStatus.PROCESSING)
        ]
        with patch.object(self.endpoint, "get_task_queue", return_value=queue):
            with self.assertRaises(HTTPException) as ctx:
                self._call(queue)
        self.assertEqual(ctx.exception.status_code, 409)
        queue.submit_background_task.assert_not_called()

    def test_allows_submission_when_previous_task_finished(self) -> None:
        queue = MagicMock()
        queue.list_pending_tasks.return_value = [
            self._task("done-screen", self.QueueTaskStatus.COMPLETED)
        ]
        queue.submit_background_task.return_value = self._task(
            "new-screen", self.QueueTaskStatus.PENDING
        )
        with (
            patch.object(self.endpoint, "get_task_queue", return_value=queue),
            patch.object(self.endpoint, "_service"),
        ):
            response = self._call(queue)
        self.assertEqual(response.task_id, "new-screen")
        queue.submit_background_task.assert_called_once()


class TaskCancellationTestCase(unittest.TestCase):
    """D8: cooperative cancellation reaches a terminal CANCELLED state."""

    def setUp(self) -> None:
        from src.services.task_queue import AnalysisTaskQueue

        self.AnalysisTaskQueue = AnalysisTaskQueue
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None
        self.queue = AnalysisTaskQueue(max_workers=1)

    def tearDown(self) -> None:
        executor = getattr(self.queue, "_executor", None)
        if executor is not None:
            executor.shutdown(wait=False)
        self.AnalysisTaskQueue._instance = self._original_instance

    def _wait_for_status(self, task_id: str, status) -> None:
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.queue.get_task(task_id).status is status:
                return
            time.sleep(0.01)
        self.fail(f"task {task_id} never reached {status}")

    def test_cancel_unknown_task_returns_none(self) -> None:
        self.assertIsNone(self.queue.cancel_task("missing-task"))

    def test_running_background_task_is_cancelled_at_next_checkpoint(self) -> None:
        from src.services.task_queue import TaskStatus

        started = threading.Event()
        release = threading.Event()

        def run_task():
            started.set()
            release.wait(timeout=5)
            return {"ok": True}

        task = self.queue.submit_background_task(
            run_task,
            stock_code="screening_screen",
            report_type="screening_screen",
        )
        self.assertTrue(started.wait(timeout=5))

        requested = self.queue.cancel_task(task.task_id)
        self.assertIs(requested.status, TaskStatus.CANCEL_REQUESTED)
        self.assertEqual(requested.message, "已请求取消，等待当前步骤结束")

        release.set()
        self._wait_for_status(task.task_id, TaskStatus.CANCELLED)
        self.assertIsNone(self.queue.get_task(task.task_id).result)

    def test_cancel_is_idempotent_for_terminal_task(self) -> None:
        from src.services.task_queue import TaskStatus

        task = self.queue.submit_background_task(
            lambda: {"ok": True},
            stock_code="x",
            report_type="screening_screen",
        )
        self._wait_for_status(task.task_id, TaskStatus.COMPLETED)

        again = self.queue.cancel_task(task.task_id)
        self.assertIs(again.status, TaskStatus.COMPLETED)


class SchedulerRestartKeepsAnalysisTestCase(unittest.TestCase):
    """D11: saving schedule config must not kill a running analysis."""

    def _scheduler(self):
        from src.services.runtime_scheduler import RuntimeSchedulerService

        scheduler = RuntimeSchedulerService(
            config_provider=MagicMock(),
            owns_schedule=True,
        )
        process = SimpleNamespace(pid=1234, poll=lambda: None)
        scheduler._analysis_process = process
        return scheduler, process

    def test_non_terminating_stop_keeps_process(self) -> None:
        scheduler, process = self._scheduler()
        with patch("src.services.runtime_scheduler._terminate_analysis_process_tree") as terminate:
            scheduler.stop(terminate_analysis=False)
        terminate.assert_not_called()
        self.assertIs(scheduler._analysis_process, process)

    def test_default_stop_terminates_process(self) -> None:
        scheduler, process = self._scheduler()
        with patch("src.services.runtime_scheduler._terminate_analysis_process_tree") as terminate:
            scheduler.stop()
        terminate.assert_called_once_with(process)


class CsvUploadLimitTestCase(unittest.TestCase):
    """D12: broker CSV uploads are capped instead of buffered without limit."""

    def _upload(self, size: int):
        from starlette.datastructures import UploadFile

        return UploadFile(file=io.BytesIO(b"a" * size), filename="trades.csv")

    def test_rejects_oversized_csv(self) -> None:
        from api.v1.endpoints.portfolio import CSV_IMPORT_MAX_BYTES, _read_csv_upload

        with self.assertRaises(HTTPException) as ctx:
            _read_csv_upload(self._upload(CSV_IMPORT_MAX_BYTES + 1))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_accepts_csv_within_limit(self) -> None:
        from api.v1.endpoints.portfolio import _read_csv_upload

        payload = _read_csv_upload(self._upload(16))
        self.assertEqual(len(payload), 16)


class PasswordChangeRotatesSessionTestCase(unittest.TestCase):
    """D12: a password change invalidates existing sessions."""

    def test_change_password_rotates_secret_and_reissues_cookie(self) -> None:
        from api.v1.endpoints import auth as auth_endpoint
        from api.v1.endpoints.auth import ChangePasswordRequest

        body = ChangePasswordRequest(
            current_password="old-pass",
            new_password="new-pass",
            new_password_confirm="new-pass",
        )
        request = MagicMock()
        with (
            patch.object(auth_endpoint, "is_password_changeable", return_value=True),
            patch.object(auth_endpoint, "change_password", return_value=None),
            patch.object(auth_endpoint, "is_auth_enabled", return_value=True),
            patch.object(auth_endpoint, "rotate_session_secret", return_value=True) as rotate,
            patch.object(auth_endpoint, "create_session", return_value="signed-session"),
            patch.object(auth_endpoint, "_set_session_cookie") as set_cookie,
        ):
            response = asyncio.run(auth_endpoint.auth_change_password(request, body))

        rotate.assert_called_once()
        set_cookie.assert_called_once_with(response, "signed-session", request)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Stage contract for task progress payloads (home run card)."""

from __future__ import annotations

import inspect
import unittest
from datetime import datetime

from src.services.analysis_progress import (
    PROGRESS_FINALIZING,
    PROGRESS_FUNDAMENTALS,
    PROGRESS_GENERATING,
    PROGRESS_MARKET_DATA,
    PROGRESS_NEWS,
    STAGE_ORDER,
    progress_stage,
    stage_index,
)
from src.services.task_queue import TaskInfo, TaskStatus


class ProgressStageTestCase(unittest.TestCase):
    def test_stage_boundaries(self) -> None:
        self.assertEqual(progress_stage(0), "queued")
        self.assertEqual(progress_stage(9), "queued")
        self.assertEqual(progress_stage(PROGRESS_MARKET_DATA), "market_data")
        self.assertEqual(progress_stage(31), "market_data")
        self.assertEqual(progress_stage(PROGRESS_FUNDAMENTALS), "fundamentals")
        self.assertEqual(progress_stage(45), "fundamentals")
        self.assertEqual(progress_stage(PROGRESS_NEWS), "news")
        self.assertEqual(progress_stage(57), "news")
        self.assertEqual(progress_stage(PROGRESS_GENERATING), "generating")
        self.assertEqual(progress_stage(92), "generating")
        self.assertEqual(progress_stage(PROGRESS_FINALIZING), "finalizing")
        self.assertEqual(progress_stage(99), "finalizing")
        self.assertEqual(progress_stage(100), "done")

    def test_stage_index_follows_the_strip_order(self) -> None:
        self.assertEqual(
            [stage_index(stage) for stage in STAGE_ORDER],
            list(range(len(STAGE_ORDER))),
        )
        self.assertEqual(stage_index("queued"), -1)

    def test_stage_is_monotonic_across_every_progress_value(self) -> None:
        seen: list[str] = []
        for value in range(0, 101):
            stage = progress_stage(value)
            if not seen or seen[-1] != stage:
                seen.append(stage)
        self.assertEqual(
            seen,
            ["queued", "market_data", "fundamentals", "news", "generating", "finalizing", "done"],
        )


class TaskPayloadStageTestCase(unittest.TestCase):
    def _task(self, **overrides) -> TaskInfo:
        now = datetime(2026, 3, 21, 8, 0, 0)
        payload = {
            "task_id": "task-1",
            "stock_code": "600519",
            "status": TaskStatus.PROCESSING,
            "progress": PROGRESS_GENERATING,
            "report_type": "detailed",
            "started_at": now,
            "progress_updated_at": now,
        }
        payload.update(overrides)
        return TaskInfo(**payload)

    def test_to_dict_carries_stage_and_timestamp(self) -> None:
        payload = self._task().to_dict()
        self.assertEqual(payload["stage"], "generating")
        self.assertEqual(payload["progress_updated_at"], "2026-03-21T08:00:00")

    def test_background_task_omits_stage(self) -> None:
        payload = self._task(
            stock_code="market_review",
            report_type="market_review",
            stage_tracked=False,
        ).to_dict()
        self.assertNotIn("stage", payload)
        self.assertIn("progress_updated_at", payload)

    def test_copy_keeps_stage_fields(self) -> None:
        original = self._task()
        copied = original.copy(include_flow_events=False)
        self.assertEqual(copied.stage_tracked, original.stage_tracked)
        self.assertEqual(copied.progress_updated_at, original.progress_updated_at)


class AgentRunProgressContractTestCase(unittest.TestCase):
    """The pipeline bridge depends on both run() signatures accepting a sink."""

    def test_executor_and_orchestrator_accept_progress_callback(self) -> None:
        from src.agent.executor import AgentExecutor
        from src.agent.orchestrator import AgentOrchestrator

        for run in (AgentExecutor.run, AgentOrchestrator.run):
            parameters = inspect.signature(run).parameters
            self.assertIn("progress_callback", parameters)
            self.assertIsNone(parameters["progress_callback"].default)


if __name__ == "__main__":
    unittest.main()

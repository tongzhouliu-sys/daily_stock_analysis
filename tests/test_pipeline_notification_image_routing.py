# -*- coding: utf-8 -*-
"""
Regression tests for daily-report notification dispatch.

T38 merged the per-channel dispatch that used to live in
``StockAnalysisPipeline._send_notifications`` into
``NotificationService.send_with_results``. These tests therefore exercise the
real notification service (with its sender methods patched) through the
pipeline entry point, so routing, image conversion and diagnostics stay
covered end to end.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.core.pipeline import StockAnalysisPipeline
from src.notification import NotificationChannel, NotificationService
from tests.support import make_pipeline
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    build_run_diagnostic_summary,
    current_diagnostic_snapshot,
    reset_run_diagnostic_context,
)
from src.enums import ReportType


ALL_CHANNELS = [
    NotificationChannel.WECHAT,
    NotificationChannel.TELEGRAM,
    NotificationChannel.EMAIL,
    NotificationChannel.NTFY,
    NotificationChannel.GOTIFY,
]


def _build_service(
    channels,
    *,
    image_channels=(),
    route_channels=None,
    noise_should_send=True,
    stock_email_groups=None,
    feishu_send_as_file=False,
):
    """Create a NotificationService wired to test doubles, without __init__."""

    service = NotificationService.__new__(NotificationService)
    service._source_message = None
    service._context_channels = []
    service._available_channels = list(channels)
    service._markdown_to_image_channels = set(image_channels)
    service._markdown_to_image_max_chars = 15000
    service._stock_email_groups = stock_email_groups or []
    service._feishu_send_as_file = feishu_send_as_file
    service._report_summary_only = False
    service._report_show_llm_model = True
    service._history_compare_cache = {}
    service.get_channels_for_route = MagicMock(
        return_value=list(route_channels if route_channels is not None else channels)
    )
    service.send_to_context = MagicMock(return_value=False)
    service.evaluate_noise_control = MagicMock(
        return_value=SimpleNamespace(
            should_send=noise_should_send,
            message="" if noise_should_send else "noise suppressed",
        )
    )
    service.record_noise_control = MagicMock()
    service.release_noise_control = MagicMock()
    service.generate_aggregate_report = MagicMock(
        side_effect=lambda results, report_type: "report:" + ",".join(r.code for r in results)
    )
    service.generate_dashboard_report = MagicMock(
        side_effect=lambda results: "report:" + ",".join(r.code for r in results)
    )
    service.generate_wechat_dashboard = MagicMock(return_value="dashboard-report")
    service.generate_brief_report = MagicMock(return_value="brief-report")
    service.save_report_to_file = MagicMock(return_value="/tmp/report.md")
    service._send_wechat_image = MagicMock(return_value=True)
    service.send_to_wechat = MagicMock(return_value=True)
    service._send_telegram_photo = MagicMock(return_value=True)
    service.send_to_telegram = MagicMock(return_value=True)
    service._send_email_with_inline_image = MagicMock(return_value=True)
    service.send_to_email = MagicMock(return_value=True)
    service.send_to_ntfy = MagicMock(return_value=True)
    service.send_to_gotify = MagicMock(return_value=True)
    service.send_to_feishu = MagicMock(return_value=True)
    service.send_feishu_file = MagicMock(return_value=True)
    return service


def _build_pipeline(service, stock_email_groups=None):
    pipeline = make_pipeline()
    pipeline.notifier = service
    pipeline.config = SimpleNamespace(stock_email_groups=stock_email_groups or [])
    # Email grouping now lives in NotificationService, so mirror the config.
    service._stock_email_groups = pipeline.config.stock_email_groups
    return pipeline


def _make_results():
    return [
        SimpleNamespace(code="000001"),
        SimpleNamespace(code="600519"),
    ]


class TestPipelineDelegatesToNotificationService(unittest.TestCase):
    def test_send_notifications_dispatches_through_send_with_results(self):
        service = _build_service([NotificationChannel.TELEGRAM])
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch.object(
            service, "send_with_results", wraps=service.send_with_results
        ) as mock_send:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["route_type"], "report")
        self.assertEqual(kwargs["results"], results)
        self.assertEqual(kwargs["report_type"], "simple")
        self.assertTrue(callable(kwargs["report_builder"]))
        self.assertEqual(kwargs["report_builder"](results), "report:000001")
        service.send_to_telegram.assert_called_once_with("report:000001")

    def test_send_notifications_records_each_channel_run_rather_than_aggregating(self):
        token = activate_run_diagnostic_context(trace_id="trace-notify")
        try:
            service = _build_service([NotificationChannel.WECHAT, NotificationChannel.TELEGRAM])
            service.send_to_telegram.return_value = False
            pipeline = _build_pipeline(service)
            results = [SimpleNamespace(code="000001")]

            pipeline._send_notifications(results, ReportType.SIMPLE)

            snapshot = current_diagnostic_snapshot() or {}
            notification_runs = snapshot.get("notification_runs", [])
            channels = [run.get("channel") for run in notification_runs]
            self.assertEqual(len(channels), 2)
            self.assertIn("wechat", channels)
            self.assertIn("telegram", channels)
            self.assertNotIn("report", channels)
        finally:
            reset_run_diagnostic_context(token)

    def test_unavailable_service_records_not_configured(self):
        service = _build_service([])
        pipeline = _build_pipeline(service)
        pipeline.save_context_snapshot = False

        pipeline._send_notifications([SimpleNamespace(code="000001")], ReportType.SIMPLE)

        service.send_to_telegram.assert_not_called()


class TestEmailGroupImageRouting(unittest.TestCase):
    def _build_group_pipeline(self, service):
        pipeline = _build_pipeline(
            service,
            stock_email_groups=[(["000001"], ["group@example.com"])],
        )
        return pipeline

    @patch("src.md2img.markdown_to_image", return_value=b"png-bytes")
    def test_email_group_uses_inline_image_when_enabled(self, _mock_md2img):
        service = _build_service(
            [NotificationChannel.EMAIL], image_channels={"email"}
        )
        pipeline = self._build_group_pipeline(service)
        results = _make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        self.assertEqual(service._send_email_with_inline_image.call_count, 2)
        service.send_to_email.assert_not_called()
        called_receivers = [
            kwargs.get("receivers")
            for _, kwargs in service._send_email_with_inline_image.call_args_list
        ]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_email_group_falls_back_to_text_when_image_unavailable(self, _mock_md2img):
        service = _build_service(
            [NotificationChannel.EMAIL], image_channels={"email"}
        )
        pipeline = self._build_group_pipeline(service)
        results = _make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service._send_email_with_inline_image.assert_not_called()
        self.assertEqual(service.send_to_email.call_count, 2)
        called_receivers = [
            kwargs.get("receivers")
            for _, kwargs in service.send_to_email.call_args_list
        ]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_email_text_fallback_strips_hidden_market_metadata(self, _mock_md2img):
        service = _build_service([NotificationChannel.EMAIL])
        service.generate_aggregate_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody"
        )
        pipeline = self._build_group_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.send_to_email.assert_called_once_with(
            "# 🎯 Market Review\n\nBody",
            receivers=["group@example.com"],
        )

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_email_group_failure_does_not_skip_later_group(self, _mock_md2img):
        service = _build_service([NotificationChannel.EMAIL])
        service.send_to_email.side_effect = [RuntimeError("group failed"), True]
        pipeline = self._build_group_pipeline(service)
        results = _make_results()

        pipeline._send_notifications(results, ReportType.SIMPLE)

        self.assertEqual(service.send_to_email.call_count, 2)
        called_receivers = [
            kwargs.get("receivers")
            for _, kwargs in service.send_to_email.call_args_list
        ]
        self.assertIn(["group@example.com"], called_receivers)
        self.assertIn(None, called_receivers)

    @patch("src.md2img.markdown_to_image", return_value=None)
    def test_email_group_diagnostics_only_patch_group_results(self, _mock_md2img):
        service = _build_service([NotificationChannel.EMAIL])
        service.send_to_email.side_effect = [RuntimeError("group failed"), True]
        pipeline = self._build_group_pipeline(service)
        pipeline.save_context_snapshot = True
        pipeline.db = MagicMock()
        results = _make_results()
        results[0].query_id = "query-group"
        results[1].query_id = "query-default"

        pipeline._send_notifications(results, ReportType.SIMPLE)

        calls = pipeline.db.update_analysis_history_diagnostics.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["query_id"], "query-group")
        self.assertEqual(calls[0].kwargs["code"], "000001")
        self.assertEqual(calls[0].kwargs["notification_runs"][0]["status"], "failed")
        self.assertEqual(calls[0].kwargs["notification_runs"][0]["channel"], "email:group@example.com")
        self.assertEqual(calls[1].kwargs["query_id"], "query-default")
        self.assertEqual(calls[1].kwargs["code"], "600519")
        self.assertEqual(calls[1].kwargs["notification_runs"][0]["status"], "success")
        self.assertEqual(calls[1].kwargs["notification_runs"][0]["channel"], "email:default")


class TestWechatOnlyImageRouting(unittest.TestCase):
    def test_wechat_only_converts_legacy_dashboard_for_image(self):
        service = _build_service([NotificationChannel.WECHAT], image_channels={"wechat"})
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"wechat-image") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_called_once_with(
            "dashboard-report", max_chars=service._markdown_to_image_max_chars
        )
        service._send_wechat_image.assert_called_once()
        service.send_to_wechat.assert_not_called()

    def test_wechat_only_logs_hint_and_falls_back_to_text(self):
        service = _build_service([NotificationChannel.WECHAT], image_channels={"wechat"})
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=None), patch(
            "src.config.get_config", return_value=SimpleNamespace(md2img_engine="wkhtmltoimage")
        ), patch("src.notification.logger.warning") as mock_warning:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        service._send_wechat_image.assert_not_called()
        service.send_to_wechat.assert_called_once_with("dashboard-report")
        self.assertTrue(
            any("企业微信 Markdown 转图片失败" in str(call.args[0]) for call in mock_warning.call_args_list)
        )


class TestReportRouteFiltering(unittest.TestCase):
    def test_applies_report_route_before_channel_iteration(self):
        service = _build_service(
            ALL_CHANNELS, route_channels=[NotificationChannel.TELEGRAM]
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.get_channels_for_route.assert_called_once_with("report")
        service.send_to_telegram.assert_called_once_with("report:000001")
        service.send_to_wechat.assert_not_called()
        service.send_to_email.assert_not_called()
        service.evaluate_noise_control.assert_called_once()
        service.record_noise_control.assert_called_once()

    def test_markdown_to_image_uses_route_filtered_channels(self):
        service = _build_service(
            ALL_CHANNELS,
            image_channels={"telegram"},
            route_channels=[NotificationChannel.EMAIL],
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        service.send_to_email.assert_called_once_with("report:000001", receivers=None)
        service.send_to_telegram.assert_not_called()

    def test_telegram_image_route_converts_full_report(self):
        service = _build_service(
            ALL_CHANNELS,
            image_channels={"telegram"},
            route_channels=[NotificationChannel.TELEGRAM],
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_called_once_with(
            "report:000001", max_chars=service._markdown_to_image_max_chars
        )
        service._send_telegram_photo.assert_called_once_with(b"png")
        service.send_to_telegram.assert_not_called()

    def test_ntfy_route_uses_text_report_without_image_conversion(self):
        service = _build_service(
            ALL_CHANNELS,
            image_channels={"ntfy"},
            route_channels=[NotificationChannel.NTFY],
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        service.send_to_ntfy.assert_called_once_with("report:000001")
        service._send_email_with_inline_image.assert_not_called()
        service._send_telegram_photo.assert_not_called()

    def test_gotify_route_uses_text_report_without_image_conversion(self):
        service = _build_service(
            ALL_CHANNELS,
            image_channels={"gotify"},
            route_channels=[NotificationChannel.GOTIFY],
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        service.send_to_gotify.assert_called_once_with("report:000001")
        service._send_email_with_inline_image.assert_not_called()
        service._send_telegram_photo.assert_not_called()

    def test_noise_suppression_happens_before_markdown_to_image(self):
        service = _build_service(
            ALL_CHANNELS,
            image_channels={"telegram"},
            route_channels=[NotificationChannel.TELEGRAM],
            noise_should_send=False,
        )
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        with patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            pipeline._send_notifications(results, ReportType.SIMPLE)

        mock_md2img.assert_not_called()
        service.send_to_telegram.assert_not_called()
        service.record_noise_control.assert_not_called()

    def test_noise_reservation_released_when_static_send_raises(self):
        service = _build_service(
            ALL_CHANNELS, route_channels=[NotificationChannel.TELEGRAM]
        )
        service.send_to_telegram.side_effect = RuntimeError("send failed")
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.record_noise_control.assert_not_called()
        service.release_noise_control.assert_called_once()

    def test_channel_exception_does_not_skip_later_channel_and_records_noise(self):
        service = _build_service(
            ALL_CHANNELS,
            route_channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
        )
        service.send_to_telegram.side_effect = RuntimeError("telegram failed")
        service.send_to_email.return_value = True
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.send_to_telegram.assert_called_once_with("report:000001")
        service.send_to_email.assert_called_once_with("report:000001", receivers=None)
        service.record_noise_control.assert_called_once()
        service.release_noise_control.assert_not_called()

    def test_all_static_channel_failures_release_noise_reservation(self):
        service = _build_service(
            ALL_CHANNELS,
            route_channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
        )
        service.send_to_telegram.side_effect = RuntimeError("telegram failed")
        service.send_to_email.return_value = False
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.send_to_telegram.assert_called_once_with("report:000001")
        service.send_to_email.assert_called_once_with("report:000001", receivers=None)
        service.record_noise_control.assert_not_called()
        service.release_noise_control.assert_called_once()

    def test_context_delivery_counts_as_success_and_is_recorded_with_routed_failures(self):
        token = activate_run_diagnostic_context(
            trace_id="trace-context",
            query_id="query-context",
            stock_code="000001",
            trigger_source="bot",
        )
        try:
            service = _build_service(
                ALL_CHANNELS, route_channels=[NotificationChannel.TELEGRAM]
            )
            service.send_to_context.return_value = True
            service.send_to_telegram.return_value = False
            pipeline = _build_pipeline(service)
            pipeline.save_context_snapshot = True
            pipeline.db = MagicMock()
            results = [SimpleNamespace(code="000001", query_id="query-context")]

            with patch("src.core.pipeline.logger.info") as mock_info:
                pipeline._send_notifications(results, ReportType.SIMPLE)
            snapshot = current_diagnostic_snapshot() or {}
        finally:
            reset_run_diagnostic_context(token)
        notification_runs = snapshot.get("notification_runs", [])
        self.assertEqual([run.get("channel") for run in notification_runs], ["__context__", "telegram"])
        self.assertTrue(notification_runs[0]["success"])
        self.assertFalse(notification_runs[1]["success"])
        self.assertTrue(
            any(
                call.args and call.args[0] == "决策仪表盘推送成功"
                for call in mock_info.call_args_list
            )
        )
        final_update = pipeline.db.update_analysis_history_diagnostics.call_args_list[-1]
        persisted_runs = final_update.kwargs["diagnostics"]["notification_runs"]
        self.assertEqual([run.get("channel") for run in persisted_runs], ["__context__", "telegram"])

    def test_context_only_delivery_skips_static_channels_in_aggregate_path(self):
        service = _build_service([NotificationChannel.TELEGRAM])
        service.send_to_context.return_value = True
        service.should_broadcast_static_channels = MagicMock(return_value=False)
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.should_broadcast_static_channels.assert_called_once_with()
        service.send_to_telegram.assert_not_called()
        service.evaluate_noise_control.assert_not_called()
        service.record_noise_control.assert_not_called()
        service.release_noise_control.assert_not_called()

    def test_notification_summary_degraded_when_only_partial_channels_fail(self):
        service = _build_service(
            ALL_CHANNELS,
            route_channels=[NotificationChannel.WECHAT, NotificationChannel.TELEGRAM],
        )
        service.send_to_wechat.return_value = True
        service.send_to_telegram.return_value = False
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        token = activate_run_diagnostic_context(
            trace_id="trace-notify",
            query_id="query-notify",
            stock_code="000001",
            trigger_source="api",
        )
        try:
            pipeline._send_notifications(results, ReportType.SIMPLE)
            snapshot = current_diagnostic_snapshot()
        finally:
            reset_run_diagnostic_context(token)

        self.assertEqual(snapshot["notification_runs"][0]["channel"], "wechat")
        self.assertEqual(snapshot["notification_runs"][0]["success"], True)
        self.assertEqual(snapshot["notification_runs"][1]["channel"], "telegram")
        self.assertEqual(snapshot["notification_runs"][1]["success"], False)

        summary = build_run_diagnostic_summary(
            context_snapshot={"diagnostics": snapshot},
            raw_result={"success": True, "model_used": "deepseek-chat"},
            report_saved=True,
        )

        self.assertEqual(summary["components"]["notification"]["status"], "degraded")
        self.assertIn(
            "telegram",
            summary["components"]["notification"]["details"]["failed"],
        )


class TestFeishuFileRouting(unittest.TestCase):
    def test_strips_hidden_market_metadata_before_saving_feishu_file(self):
        service = _build_service(
            [NotificationChannel.FEISHU], feishu_send_as_file=True
        )
        service.generate_aggregate_report = MagicMock(
            return_value="[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"
        )
        service.save_report_to_file = MagicMock(return_value="/tmp/dashboard_20260802.md")
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.SIMPLE)

        service.save_report_to_file.assert_called_once_with(
            "# 市场复盘\n\n正文",
            filename=mock.ANY,
        )
        service.send_feishu_file.assert_called_once_with("/tmp/dashboard_20260802.md")
        service.send_to_feishu.assert_not_called()


class TestEveryConfiguredChannelReceivesReport(unittest.TestCase):
    """T38 acceptance: configuring a channel is enough to receive the report."""

    CASES = {
        NotificationChannel.WECHAT: ("send_to_wechat", "_send_wechat_image"),
        NotificationChannel.DINGTALK: ("send_to_dingtalk", None),
        NotificationChannel.FEISHU: ("send_to_feishu", None),
        NotificationChannel.TELEGRAM: ("send_to_telegram", "_send_telegram_photo"),
        NotificationChannel.EMAIL: ("send_to_email", None),
        NotificationChannel.PUSHOVER: ("send_to_pushover", None),
        NotificationChannel.NTFY: ("send_to_ntfy", None),
        NotificationChannel.GOTIFY: ("send_to_gotify", None),
        NotificationChannel.PUSHPLUS: ("send_to_pushplus", None),
        NotificationChannel.SERVERCHAN3: ("send_to_serverchan3", None),
        NotificationChannel.CUSTOM: ("send_to_custom", None),
        NotificationChannel.DISCORD: ("send_to_discord", None),
        NotificationChannel.SLACK: ("send_to_slack", None),
        NotificationChannel.ASTRBOT: ("send_to_astrbot", None),
    }

    def test_each_configured_channel_receives_report(self):
        for channel, (text_method, image_method) in self.CASES.items():
            with self.subTest(channel=channel.value):
                service = _build_service([channel])
                sender = MagicMock(return_value=True)
                setattr(service, text_method, sender)
                pipeline = _build_pipeline(service)
                results = [SimpleNamespace(code="000001")]

                with patch("src.md2img.markdown_to_image", return_value=None):
                    pipeline._send_notifications(results, ReportType.SIMPLE)

                sender.assert_called_once()
                if image_method is not None:
                    getattr(service, image_method).assert_not_called()


class TestWechatDashboardContent(unittest.TestCase):
    def test_brief_report_uses_brief_content_for_wechat(self):
        service = _build_service([NotificationChannel.WECHAT])
        pipeline = _build_pipeline(service)
        results = [SimpleNamespace(code="000001")]

        pipeline._send_notifications(results, ReportType.BRIEF)

        service.generate_brief_report.assert_called_once()
        service.generate_wechat_dashboard.assert_not_called()
        service.send_to_wechat.assert_called_once_with("brief-report")


if __name__ == "__main__":
    unittest.main()

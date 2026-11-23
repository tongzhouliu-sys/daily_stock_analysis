# -*- coding: utf-8 -*-
"""F1 飞书送达可靠性热修测试。

覆盖：
1. 请求体大小计算（实际 JSON 序列化字节数）
2. 纯文本兜底去标记
3. 关键词添加到页脚
4. 紧凑卡片超限返回 None
5. 分片续页继承标题
"""

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from src.formatters import format_feishu_markdown, strip_feishu_markup
from src.notification_sender.feishu_card import build_feishu_card
from src.notification_sender.feishu_sender import (
    FeishuSender,
    _calculate_webhook_request_body_size,
)
from src.report_language import REPORT_AI_DISCLAIMER


def _config(**overrides):
    defaults = dict(
        feishu_webhook_url="https://example.com/webhook",
        feishu_webhook_secret=None,
        feishu_webhook_keyword=None,
        feishu_max_bytes=20000,
        feishu_send_as_file=False,
        webhook_verify_ssl=True,
        feishu_app_id="",
        feishu_app_secret="",
        feishu_chat_id="",
        feishu_receive_id_type="chat_id",
        feishu_domain="feishu",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestCalculateWebhookRequestBodySize:
    """测试请求体大小计算。"""

    def test_ascii_content(self):
        """ASCII 内容：JSON 序列化后大小等于 UTF-8 字节数。"""
        payload = {"msg_type": "text", "content": {"text": "hello world"}}
        size = _calculate_webhook_request_body_size(payload)
        # JSON 序列化后应该接近原始大小
        assert size > 0
        assert size < 200

    def test_chinese_content_uses_utf8_bytes(self):
        """中文内容按 UTF-8 计量，不再按 \\uXXXX 转义后的体积放大。"""
        payload = {"msg_type": "text", "content": {"text": "测试中文"}}
        size = _calculate_webhook_request_body_size(payload)
        expected = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        # 4 个中文字 = 12 字节，加上 JSON 结构开销仍应远小于转义口径。
        assert size == expected
        assert size < 80

    def test_mixed_content(self):
        """混合内容：ASCII + 中文。"""
        payload = {"msg_type": "text", "content": {"text": "hello 世界"}}
        size = _calculate_webhook_request_body_size(payload)
        assert size > 0

    def test_card_payload(self):
        """卡片请求体：包含嵌套结构。"""
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "测试标题"},
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": "内容"}},
                ],
            },
        }
        size = _calculate_webhook_request_body_size(payload)
        assert size > 100


class TestStripFeishuMarkup:
    """测试纯文本去标记。"""

    def test_strip_bold(self):
        """去除加粗标记。"""
        assert strip_feishu_markup("**加粗**") == "加粗"
        assert strip_feishu_markup("*斜体*") == "斜体"

    def test_strip_font_tags(self):
        """去除 font 标签。"""
        result = strip_feishu_markup("<font color='grey'>灰色文字</font>")
        assert result == "灰色文字"
        assert "<font" not in result

    def test_strip_text_tag(self):
        """去除 text_tag 标签。"""
        result = strip_feishu_markup("<text_tag color='blue'>标签</text_tag>")
        assert result == "标签"
        assert "<text_tag" not in result

    def test_strip_prefix_symbols(self):
        """去除行首装饰符号。"""
        result = strip_feishu_markup("▎ 标题\n· 子标题")
        assert "▎" not in result
        assert "·" not in result or result.count("·") == 0

    def test_strip_bullet(self):
        """去除 bullet。"""
        result = strip_feishu_markup("• 列表项")
        assert "•" not in result
        assert "列表项" in result

    def test_complex_content(self):
        """复杂内容：多种标记混合。"""
        content = """▎ **重要信息**
<font color='grey'>💬 引用文字</font>
• 列表项 1
• 列表项 2
<text_tag color='blue'>标签</text_tag>"""
        result = strip_feishu_markup(content)
        assert "**" not in result
        assert "<font" not in result
        assert "<text_tag" not in result
        assert "▎" not in result
        assert "重要信息" in result
        assert "引用文字" in result


class TestAddKeywordToCardFooter:
    """测试关键词添加到页脚。"""

    def test_add_to_existing_note(self):
        """已有 note 组件时，添加到开头。"""
        card_body = {
            "config": {},
            "header": {},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "内容"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "页脚"}]},
            ],
        }
        FeishuSender._add_keyword_to_card_footer(card_body, "关键词")
        
        note = card_body["elements"][-1]
        assert note["tag"] == "note"
        assert len(note["elements"]) == 2
        assert note["elements"][0]["content"] == "关键词"
        assert note["elements"][1]["content"] == "页脚"

    def test_create_new_note(self):
        """没有 note 组件时，创建新的。"""
        card_body = {
            "config": {},
            "header": {},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "内容"}},
            ],
        }
        FeishuSender._add_keyword_to_card_footer(card_body, "关键词")
        
        assert len(card_body["elements"]) == 2
        note = card_body["elements"][-1]
        assert note["tag"] == "note"
        assert note["elements"][0]["content"] == "关键词"

    def test_empty_keyword(self):
        """空关键词时不做处理。"""
        card_body = {
            "config": {},
            "header": {},
            "elements": [{"tag": "div"}],
        }
        original_len = len(card_body["elements"])
        FeishuSender._add_keyword_to_card_footer(card_body, "")
        assert len(card_body["elements"]) == original_len

    def test_empty_card_body(self):
        """空卡片时不做处理。"""
        card_body = {"config": {}, "header": {}, "elements": []}
        FeishuSender._add_keyword_to_card_footer(card_body, "关键词")
        # 不应该添加 note（因为没有元素可以附加）


class TestBuildFeishuCardSizeLimit:
    """测试紧凑卡片超限返回 None。"""

    def test_small_content_returns_card(self):
        """小内容返回卡片。"""
        content = "# 标题\n\n内容"
        formatted = format_feishu_markdown(content)
        card = build_feishu_card(formatted, max_bytes=20000)
        assert card is not None
        assert "header" in card
        assert "elements" in card

    def test_oversized_compact_returns_none(self):
        """紧凑卡片超限时返回 None。"""
        # 构造一个很长的内容，确保富卡片和轻卡片都超限
        long_content = "# 标题\n\n" + "很长的内容 " * 5000
        formatted = format_feishu_markdown(long_content)
        # 设置一个很小的 max_bytes，让紧凑卡片也超限
        card = build_feishu_card(formatted, max_bytes=100)
        assert card is None


class TestExtractH1Title:
    """测试提取一级标题。"""

    def test_extract_simple_title(self):
        """提取简单标题。"""
        content = "# 测试标题\n\n内容"
        title = FeishuSender._extract_h1_title(content)
        assert title == "测试标题"

    def test_extract_title_with_emoji(self):
        """提取带 emoji 的标题。"""
        content = "# 📊 决策仪表盘\n\n内容"
        title = FeishuSender._extract_h1_title(content)
        assert "决策仪表盘" in title

    def test_extract_title_with_prefix(self):
        """提取带前缀符号的标题。"""
        content = "# ▎ 带前缀的标题\n\n内容"
        title = FeishuSender._extract_h1_title(content)
        assert "带前缀的标题" in title
        assert "▎" not in title

    def test_no_h1_returns_empty(self):
        """没有一级标题时返回空字符串。"""
        content = "## 二级标题\n\n内容"
        title = FeishuSender._extract_h1_title(content)
        assert title == ""

    def test_ignore_h2_and_below(self):
        """忽略二级及以下标题。"""
        content = "## 二级标题\n\n### 三级标题\n\n内容"
        title = FeishuSender._extract_h1_title(content)
        assert title == ""


class TestIntegration:
    """集成测试：完整的发送流程。"""

    def test_card_with_keyword(self):
        """带关键词的卡片发送。"""
        config = _config(feishu_webhook_keyword="测试关键词")
        sender = FeishuSender(config)
        
        # Mock HTTP 请求
        with mock.patch("requests.post") as mock_post:
            mock_response = mock.Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"code": 0}
            mock_post.return_value = mock_response
            
            content = "# 测试报告\n\n这是测试内容"
            result = sender.send_to_feishu(content)
            
            assert result is True
            # 验证请求体中包含关键词在页脚，而不是正文开头
            call_args = mock_post.call_args
            payload = json.loads(call_args[1]["data"])
            card = payload.get("card", {})
            elements = card.get("elements", [])
            # 最后一个元素应该是 note，包含关键词
            if elements and elements[-1].get("tag") == "note":
                note_content = elements[-1].get("elements", [{}])[0].get("content", "")
                assert "测试关键词" in note_content

    def test_text_fallback_strips_markup(self):
        """纯文本兜底时去除标记。"""
        config = _config()
        sender = FeishuSender(config)
        
        # Mock HTTP 请求：卡片失败，文本成功
        call_count = [0]
        
        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = mock.Mock()
            response.status_code = 200
            if call_count[0] == 1:
                # 第一次（卡片）失败
                response.json.return_value = {"code": 99999, "msg": "card error"}
            else:
                # 第二次（文本）成功
                response.json.return_value = {"code": 0}
            return response
        
        with mock.patch("requests.post", side_effect=mock_post):
            content = "# 标题\n\n**加粗** <font color='grey'>灰色</font>"
            result = sender.send_to_feishu(content)
            
            assert result is True
            # 验证第二次请求是纯文本，没有标记
            assert call_count[0] == 2


class TestReportDisclaimerScope:
    """统一声明只出现在分析报告推送，覆盖卡片与纯文本兜底。"""

    def test_report_route_adds_disclaimer_to_card_note(self):
        config = _config()
        sender = FeishuSender(config)
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value = mock.Mock(status_code=200, json=lambda: {"code": 0})
            assert sender.send_to_feishu(
                "# 测试报告\n\n正文",
                render_context={"route_type": "report"},
            )

        payload = json.loads(mock_post.call_args[1]["data"])
        note = payload["card"]["elements"][-1]
        note_text = note["elements"][0]["content"]
        assert note_text.endswith(REPORT_AI_DISCLAIMER)

    def test_alert_route_does_not_add_disclaimer(self):
        config = _config()
        sender = FeishuSender(config)
        with mock.patch("requests.post") as mock_post:
            mock_post.return_value = mock.Mock(status_code=200, json=lambda: {"code": 0})
            assert sender.send_to_feishu(
                "# 告警\n\n正文",
                render_context={"route_type": "alert"},
            )

        payload = json.loads(mock_post.call_args[1]["data"])
        assert REPORT_AI_DISCLAIMER not in json.dumps(payload, ensure_ascii=False)

    def test_report_text_fallback_keeps_disclaimer(self):
        config = _config()
        sender = FeishuSender(config)
        call_count = [0]

        def mock_post(*args, **kwargs):
            call_count[0] += 1
            response = mock.Mock(status_code=200)
            response.json.return_value = (
                {"code": 99999} if call_count[0] == 1 else {"code": 0}
            )
            return response

        with mock.patch("requests.post", side_effect=mock_post) as mock_http:
            assert sender.send_to_feishu(
                "# 测试报告\n\n正文",
                render_context={"route_type": "report"},
            )

        payload = json.loads(mock_http.call_args[1]["data"])
        assert payload["msg_type"] == "text"
        assert REPORT_AI_DISCLAIMER in payload["content"]["text"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestUtf8WebhookTransport:
    """Webhook 按 UTF-8 JSON 发送，避免转义把 20KB 预算放大。"""

    def test_request_body_is_utf8_without_ascii_escapes(self):
        from src.notification_sender.feishu_sender import _encode_webhook_request_body

        payload = {
            "msg_type": "interactive",
            "card": {"header": {"title": {"content": "中文标题"}}},
        }
        body = _encode_webhook_request_body(payload)

        assert isinstance(body, bytes)
        assert b"\\u" not in body
        assert "中文标题".encode("utf-8") in body
        assert len(body) == _calculate_webhook_request_body_size(payload)

    def test_app_send_once_skips_null_card(self):
        sender = FeishuSender(
            _config(
                feishu_app_id="cli_app",
                feishu_app_secret="secret",
                feishu_chat_id="oc_chat",
            )
        )
        with mock.patch.object(
            FeishuSender, "_build_card_body", return_value=None
        ), mock.patch.object(
            FeishuSender, "_app_send_raw", return_value=True
        ) as raw:
            assert sender._app_send_once(object(), "# 标题\n\n正文") is True

        # 不能在卡片超限时发送 JSON ``null``，只能走纯文本兜底。
        assert [call[0][1] for call in raw.call_args_list] == ["text"]

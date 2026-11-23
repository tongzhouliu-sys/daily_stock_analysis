# -*- coding: utf-8 -*-
"""
飞书 发送提醒服务

职责：
1. 通过 webhook 发送飞书消息
2. 通过飞书应用机器人（App Bot）发送消息（lark-oapi SDK）
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import threading
import time
import uuid as uuid_mod
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from src.config import Config
from src.formatters import (
    MIN_MAX_BYTES,
    PAGE_MARKER_SAFE_BYTES,
    chunk_content_by_max_bytes,
    format_feishu_markdown,
    strip_feishu_markup,
    strip_hidden_markdown_metadata,
)
from src.notification_sender.feishu_card import build_feishu_card
from src.report_language import ensure_report_disclaimer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# lark-oapi SDK availability
# ---------------------------------------------------------------------------

FEISHU_SDK_AVAILABLE = False
_lark: Any = None  # type: ignore[assignment]
FEISHU_DOMAIN = "feishu"
LARK_DOMAIN = "lark"
try:
    import lark_oapi as _lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )
    from lark_oapi.core.const import FEISHU_DOMAIN as _SDK_FEISHU_DOMAIN
    from lark_oapi.core.const import LARK_DOMAIN as _SDK_LARK_DOMAIN

    FEISHU_DOMAIN = _SDK_FEISHU_DOMAIN
    LARK_DOMAIN = _SDK_LARK_DOMAIN
    FEISHU_SDK_AVAILABLE = True
except ImportError:
    pass

# File-upload SDK classes (isolated from the core messaging SDK availability
# so that an older lark-oapi without file support doesn't break App Bot text).
FEISHU_FILE_SDK_AVAILABLE = False
_CreateFileRequest: Any = None
_CreateFileRequestBody: Any = None
try:
    from lark_oapi.api.im.v1 import (
        CreateFileRequest as _CreateFileRequest,
        CreateFileRequestBody as _CreateFileRequestBody,
    )
    FEISHU_FILE_SDK_AVAILABLE = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APP_SEND_RETRIES = 3
_APP_SEND_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_WEBHOOK_SEND_TIMEOUT_SECONDS = 30
_WEBHOOK_RATE_LIMIT_RETRIES = 2
_WEBHOOK_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 5.0)
_WEBHOOK_RATE_LIMIT_ERROR_CODES = (11232, 11233)  # 飞书限流错误码

# Webhook 外层 msg_type / card / timestamp / sign 的序列化开销预留。
_WEBHOOK_ENVELOPE_RESERVE_BYTES = 512

# Sentinel for "client not yet initialised".
_NO_CLIENT = object()


def _encode_webhook_request_body(payload: Dict[str, Any]) -> bytes:
    """把 Webhook 请求体序列化为 UTF-8 JSON 字节。

    飞书自定义机器人允许标准 UTF-8 JSON，中文按 3 字节发送；不要用
    ``requests.post(json=...)`` 的默认行为，它会把中文转义成 ``\\uXXXX``
    （6 字节/字符），让卡片真实体积比预算大 1.4 倍以上。
    """
    json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json_str.encode("utf-8")


def _calculate_webhook_request_body_size(payload: Dict[str, Any]) -> int:
    """计算 Webhook 实际请求体大小（字节）。

    与实际发送保持一致：UTF-8 JSON，不转义非 ASCII 字符。飞书 20 KB
    限制计量的是请求体字节数，因此预算必须按同一口径计算。

    Args:
        payload: 完整的请求体字典（包含 msg_type, card/text, timestamp, sign 等）

    Returns:
        序列化后的字节数
    """
    try:
        return len(_encode_webhook_request_body(payload))
    except (TypeError, ValueError):
        return 0


class FeishuSender:

    def __init__(self, config: Config):
        """
        Initialise Feishu sender.

        Two mutually exclusive routing modes are supported:
          1. **Webhook** – configured via ``feishu_webhook_url`` (legacy).
          2. **App Bot** – configured via ``feishu_app_id`` + ``feishu_app_secret``
             + ``feishu_chat_id``, sends through the ``lark-oapi`` SDK.

        Webhook mode takes precedence when both are configured.
        """
        # -- Webhook mode --
        self._feishu_url = getattr(config, "feishu_webhook_url", None)
        self._feishu_secret = (getattr(config, "feishu_webhook_secret", None) or "").strip()
        self._feishu_keyword = (getattr(config, "feishu_webhook_keyword", None) or "").strip()
        self._feishu_max_bytes = getattr(config, "feishu_max_bytes", 20000)
        self._feishu_send_as_file = getattr(config, "feishu_send_as_file", False)
        self._webhook_verify_ssl = getattr(config, "webhook_verify_ssl", True)

        # -- App Bot mode --
        self._feishu_app_id = (getattr(config, "feishu_app_id", None) or "").strip()
        self._feishu_app_secret = (getattr(config, "feishu_app_secret", None) or "").strip()
        self._feishu_chat_id = (getattr(config, "feishu_chat_id", None) or "").strip()
        self._feishu_receive_id_type = (
            getattr(config, "feishu_receive_id_type", None) or "chat_id"
        ).strip().lower()
        if self._feishu_receive_id_type not in ("chat_id", "open_id"):
            logger.warning(
                "无效的 FEISHU_RECEIVE_ID_TYPE=%s，回退为 chat_id",
                self._feishu_receive_id_type,
            )
            self._feishu_receive_id_type = "chat_id"
        # domain_name must be "feishu" or "lark"; anything else defaulted to feishu.
        raw_domain = (
            getattr(config, "feishu_domain", None) or os.getenv("FEISHU_DOMAIN", "feishu")
        ).strip().lower()
        if raw_domain not in ("feishu", "lark"):
            logger.warning(
                "无效的 FEISHU_DOMAIN=%s，回退为 feishu", raw_domain
            )
            raw_domain = "feishu"
        self._feishu_domain = FEISHU_DOMAIN if raw_domain == "feishu" else LARK_DOMAIN

        self._app_client: Any = _NO_CLIENT
        self._app_client_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_card_body(
        content: str,
        *,
        max_bytes: Optional[int] = None,
        include_disclaimer: bool = False,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Build a Feishu interactive-card body (without the ``msg_type`` wrapper).

        Long reports are rendered as structured cards; short messages and cards
        exceeding ``max_bytes`` fall back to a compact single-div card.  The
        original content is never dropped, so the existing text fallback still
        works unchanged.

        ``render_context`` 携带通知链路已算好的结构化数据；有值时卡片顶部渲染
        信号区，缺省时行为与旧版逐字节一致。
        """
        return build_feishu_card(
            content,
            max_bytes=max_bytes,
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )

    # ------------------------------------------------------------------
    # Webhook helpers (unchanged legacy path)
    # ------------------------------------------------------------------

    def _get_keyword_prefix(self) -> str:
        if not self._feishu_keyword:
            return ""
        return f"{self._feishu_keyword}\n"

    def _apply_keyword_prefix(self, content: str) -> str:
        prefix = self._get_keyword_prefix()
        if not prefix:
            return content
        return f"{prefix}{content}" if content else self._feishu_keyword

    @staticmethod
    def _add_keyword_to_card_footer(card_body: Dict[str, Any], keyword: str) -> None:
        """将关键词添加到卡片的页脚 note 组件中，用于通过飞书关键词校验。

        关键词不再参与正文解析，避免破坏标题识别。

        Args:
            card_body: 卡片 JSON body（不含 msg_type）
            keyword: 飞书机器人关键词
        """
        if not keyword or not card_body:
            return
        
        elements = card_body.get("elements", [])
        if not elements:
            return
        
        # 查找最后一个 note 组件
        last_element = elements[-1] if elements else None
        if last_element and last_element.get("tag") == "note":
            # 在现有 note 中添加关键词
            note_elements = last_element.get("elements", [])
            note_elements.insert(0, {"tag": "plain_text", "content": keyword})
        else:
            # 创建新的 note 组件
            elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": keyword}],
            })

    def _build_security_fields(self) -> Dict[str, str]:
        if not self._feishu_secret:
            return {}
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self._feishu_secret}"
        sign = base64.b64encode(
            hmac.new(
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {"timestamp": timestamp, "sign": sign}

    # ------------------------------------------------------------------
    # App Bot client (lazy, thread-safe)
    # ------------------------------------------------------------------

    def _ensure_app_client(self) -> Any:
        """Lazily initialise the ``lark-oapi`` client for App Bot mode."""
        if self._app_client is not _NO_CLIENT:
            return self._app_client
        with self._app_client_lock:
            if self._app_client is not _NO_CLIENT:
                return self._app_client
            if not FEISHU_SDK_AVAILABLE:
                logger.warning(
                    "飞书 App Bot 需要 lark-oapi 库；标准安装请运行: pip install -r requirements.txt"
                )
                self._app_client = None
                return None
            if not self._feishu_app_id or not self._feishu_app_secret:
                missing = []
                if not self._feishu_app_id:
                    missing.append("FEISHU_APP_ID")
                if not self._feishu_app_secret:
                    missing.append("FEISHU_APP_SECRET")
                logger.warning("飞书 App Bot 凭据不全，缺少: %s", ", ".join(missing))
                self._app_client = None
                return None
            try:
                self._app_client = (
                    _lark.Client.builder()
                    .app_id(self._feishu_app_id)
                    .app_secret(self._feishu_app_secret)
                    .domain(self._feishu_domain)
                    .log_level(_lark.LogLevel.WARNING)
                    .build()
                )
                logger.info("飞书 App Bot 客户端初始化成功 (domain=%s)", self._feishu_domain)
            except Exception as e:
                logger.error("飞书 App Bot 客户端初始化失败: %s", e)
                self._app_client = None
            return self._app_client

    # ------------------------------------------------------------------
    # App Bot send helpers
    # ------------------------------------------------------------------

    def _send_via_app_bot(
        self,
        content: str,
        render_context: Optional[Dict[str, Any]] = None,
        *,
        include_disclaimer: bool = False,
    ) -> bool:
        """Send message through the Feishu App Bot, chunking if necessary.

        Args:
            content: 消息内容
            render_context: 渲染上下文，用于结构化卡片渲染
            include_disclaimer: 分析报告推送为 True，页脚补统一声明
        """
        if not self._feishu_chat_id:
            logger.warning("FEISHU_CHAT_ID 未配置，跳过 App Bot 推送")
            return False

        client = self._ensure_app_client()
        if client is None:
            return False

        formatted = format_feishu_markdown(content)
        content_bytes = len(formatted.encode("utf-8"))

        if content_bytes > self._feishu_max_bytes:
            logger.info(
                "App Bot 消息超长 (%d 字节)，将分批发送", content_bytes
            )
            return self._app_send_chunked(
                client,
                formatted,
                include_disclaimer=include_disclaimer,
                render_context=render_context,
            )

        return self._app_send_once(
            client,
            formatted,
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )

    def _app_send_chunked(
        self,
        client: Any,
        content: str,
        *,
        include_disclaimer: bool = False,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Chunk and send long content through App Bot."""
        try:
            chunks = chunk_content_by_max_bytes(
                content, self._feishu_max_bytes, add_page_marker=True
            )
        except (ValueError, TypeError, Exception) as e:
            logger.error("App Bot 分片失败: %s", e)
            return False

        success = True
        for i, chunk in enumerate(chunks):
            ok = self._app_send_once(
                client,
                chunk,
                include_disclaimer=include_disclaimer,
                render_context=render_context if i == 0 else None,
            )
            if not ok:
                logger.error("App Bot 第 %d/%d 批发送失败", i + 1, len(chunks))
                success = False
            if i < len(chunks) - 1:
                time.sleep(1)
        return success

    def _app_send_once(
        self,
        client: Any,
        content: str,
        *,
        include_disclaimer: bool = False,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Single-shot send via App Bot with card-first / text-fallback.

        Content received here has already been through ``format_feishu_markdown``
        which converts all Markdown constructs to ``lark_md``-compatible format.
        The interactive card uses ``tag: lark_md`` for rendering.
        """
        card_body = self._build_card_body(
            content,
            max_bytes=max(1, self._feishu_max_bytes - _WEBHOOK_ENVELOPE_RESERVE_BYTES),
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )

        if card_body is not None:
            # 与 _card_size() / Webhook 预算使用同一序列化口径，避免实际
            # App Bot payload 因默认分隔符空格比预算多出约 1KB。
            card_payload = json.dumps(
                card_body, ensure_ascii=False, separators=(",", ":")
            )
            if self._app_send_raw(client, "interactive", card_payload):
                return True

        # Fallback to plain text. Never send a JSON ``null`` card when the
        # compact card could not fit the budget.
        text_content = ensure_report_disclaimer(content) if include_disclaimer else content
        text_payload = json.dumps(
            {"text": text_content}, ensure_ascii=False, separators=(",", ":")
        )
        return self._app_send_raw(client, "text", text_payload)

    def _app_send_raw(self, client: Any, msg_type: str, content_json: str) -> bool:
        """Low-level send via lark-oapi SDK with retry and idempotency UUID.

        Request construction is done once outside the retry loop; it is
        deterministic and a construction error is a programming error, not
        a transient failure.
        """
        if client is None:
            return False

        send_uuid = str(uuid_mod.uuid4())
        try:
            req = (
                CreateMessageRequest.builder()
                .receive_id_type(self._feishu_receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(self._feishu_chat_id)
                    .content(content_json)
                    .msg_type(msg_type)
                    .uuid(send_uuid)
                    .build()
                )
                .build()
            )
        except Exception as e:
            logger.error("App Bot 请求构建失败: %s: %s", type(e).__name__, e)
            return False

        last_status: Optional[str] = None

        for attempt in range(_APP_SEND_RETRIES):
            try:
                resp = client.im.v1.message.create(req)
            except Exception as e:
                logger.warning(
                    "App Bot 发送异常 (attempt=%d/%d): %s: %s",
                    attempt + 1, _APP_SEND_RETRIES, type(e).__name__, e,
                )
                if attempt < _APP_SEND_RETRIES - 1:
                    time.sleep(
                        _APP_SEND_BACKOFF_SECONDS[
                            min(attempt, len(_APP_SEND_BACKOFF_SECONDS) - 1)
                        ]
                    )
                continue

            if resp.success():
                logger.info("App Bot 消息发送成功 (type=%s)", msg_type)
                return True

            try:
                log_id = resp.get_log_id()
            except (AttributeError, Exception):
                log_id = "N/A"
            status = "code=%s, msg=%s, log_id=%s" % (
                resp.code, resp.msg, log_id,
            )
            last_status = status
            logger.warning(
                "App Bot 发送失败 (attempt=%d/%d): %s",
                attempt + 1, _APP_SEND_RETRIES, status,
            )

            if attempt < _APP_SEND_RETRIES - 1:
                time.sleep(
                    _APP_SEND_BACKOFF_SECONDS[
                        min(attempt, len(_APP_SEND_BACKOFF_SECONDS) - 1)
                    ]
                )

        if last_status:
            logger.error("App Bot 发送最终失败: %s", last_status)
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_to_feishu(
        self,
        content: str,
        *,
        timeout_seconds: Optional[float] = None,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Push a message to Feishu.

        Routing priority:
          1. **Webhook** – when ``feishu_webhook_url`` is configured.
          2. **App Bot** – when ``feishu_app_id`` + ``feishu_app_secret``
             + ``feishu_chat_id`` are all configured and webhook is absent.

        Args:
            content: 消息内容（Markdown 格式）
            timeout_seconds: Webhook 请求超时时间
            render_context: 可选的渲染上下文，包含 route_type / severity /
                report_type / results / structured_payload 等信息，用于
                结构化卡片渲染。不传时行为与旧版一致。

        Returns:
            Whether the send succeeded.
        """
        if content is None:
            logger.error("send_to_feishu: content 不能为 None")
            return False
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        # 仅分析报告推送补统一声明；告警、测试消息等保持原样。
        include_disclaimer = bool(
            render_context and render_context.get("route_type") == "report"
        )
        if self._feishu_url:
            return self._send_via_webhook(
                sanitized_content,
                timeout_seconds=timeout_seconds,
                render_context=render_context,
                include_disclaimer=include_disclaimer,
            )
        return self._send_via_app_bot(
            sanitized_content,
            render_context=render_context,
            include_disclaimer=include_disclaimer,
        )

    def send_feishu_file(self, file_path: str) -> bool:
        """
        Upload and send a file to the Feishu chat.

        .. note::

           * **App Bot mode** – uploads the file via the lark-oapi SDK and
             sends it as a file message.  This is the recommended path.
           * **Webhook mode** – reads the file content and sends it as a
             regular text/card message (webhooks do not support file upload).

        Args:
            file_path: Absolute or relative path to the local file.

        Returns:
            Whether the send succeeded.
        """
        path = Path(file_path)
        if not path.is_file():
            logger.error("send_feishu_file: 文件不存在: %s", file_path)
            return False

        upload_path, display_name = self._prepare_sanitized_file(path)
        try:
            if self._feishu_url:
                # Webhook mode: send file content as a message (best-effort).
                return self._send_file_via_webhook(upload_path, display_name=display_name)

            # App Bot mode: upload file via SDK.
            return self._send_file_via_app_bot(upload_path, display_name=display_name)
        finally:
            if upload_path != path:
                try:
                    upload_path.unlink()
                except OSError:
                    logger.warning("清理飞书临时文件失败: %s", upload_path)

    @staticmethod
    def _prepare_sanitized_file(path: Path) -> Tuple[Path, str]:
        display_name = path.name
        if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            return path, display_name

        try:
            raw_text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            return path, display_name
        if not isinstance(raw_text, str):
            return path, display_name

        sanitized_text = strip_hidden_markdown_metadata(raw_text).strip()
        if sanitized_text == raw_text:
            return path, display_name

        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=path.suffix,
            prefix="feishu_sanitized_",
            delete=False,
        )
        with temp_file:
            temp_file.write(sanitized_text)
        return Path(temp_file.name), display_name

    def _send_file_via_app_bot(self, path: Path, *, display_name: Optional[str] = None) -> bool:
        """Upload *path* to Feishu via App Bot SDK and send as file message."""
        if not FEISHU_FILE_SDK_AVAILABLE:
            logger.warning("lark-oapi SDK does not support file upload; upgrade lark-oapi")
            return False

        if not self._feishu_chat_id:
            logger.warning("FEISHU_CHAT_ID 未配置，跳过 App Bot 文件推送")
            return False

        client = self._ensure_app_client()
        if client is None:
            return False

        file_name = display_name or path.name
        # Determine file_type from extension; fall back to "stream" for unknown types.
        feishu_file_types = {
            ".opus": "opus", ".aac": "aac", ".amr": "amr", ".mp3": "mp3",
            ".wma": "wma", ".pcm": "pcm", ".wav": "wav",
            ".mp4": "mp4", ".gif": "gif",
            ".pdf": "pdf",
            ".doc": "doc", ".docx": "docx",
            ".xls": "xls", ".xlsx": "xlsx",
            ".ppt": "ppt", ".pptx": "pptx",
        }
        file_type = feishu_file_types.get(path.suffix.lower(), "stream")

        try:
            with path.open("rb") as f:
                body = (
                    _CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(file_name)
                    .file(f)  # type: ignore[arg-type]
                    .build()
                )
                req = (
                    _CreateFileRequest.builder()
                    .request_body(body)
                    .build()
                )
                resp = client.im.v1.file.create(req)
        except Exception as e:
            logger.error("App Bot 文件上传异常: %s: %s", type(e).__name__, e)
            return False

        if not resp.success():
            try:
                log_id = resp.get_log_id()
            except (AttributeError, Exception):
                log_id = "N/A"
            logger.error(
                "App Bot 文件上传失败: code=%s, msg=%s, log_id=%s",
                resp.code, resp.msg, log_id,
            )
            return False

        file_key = resp.data.file_key if resp.data else None
        if not file_key:
            logger.error("App Bot 文件上传成功但未返回 file_key")
            return False

        logger.info("App Bot 文件上传成功: file_key=%s, file_name=%s", file_key, file_name)

        # Send a file message with the uploaded file_key.
        content_json = json.dumps({"file_key": file_key})
        return self._app_send_raw(client, "file", content_json)

    @staticmethod
    def _guess_mime_for_webhook(path: Path) -> str:
        """Determine a human-readable label for webhook fallback."""
        suffix = path.suffix.lower()
        labels = {".md": "Markdown", ".txt": "文本", ".pdf": "PDF", ".csv": "CSV"}
        return labels.get(suffix, suffix.lstrip(".").upper() or "文件")

    def _send_file_via_webhook(self, path: Path, *, display_name: Optional[str] = None) -> bool:
        """Send file *content* as a Feishu message (webhook fallback)."""
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.error("读取文件内容失败 (webhook fallback): %s: %s", type(e).__name__, e)
            return False

        file_label = self._guess_mime_for_webhook(path)
        header = f"**📄 {file_label} 文件内容: {display_name or path.name}**\n\n"
        content = header + strip_hidden_markdown_metadata(text).strip()
        # Delegate to the existing webhook send path.
        return self._send_via_webhook(content)

    # ------------------------------------------------------------------
    # Webhook path (legacy, unchanged)
    # ------------------------------------------------------------------

    def _send_via_webhook(
        self,
        content: str,
        *,
        timeout_seconds: Optional[float] = None,
        render_context: Optional[Dict[str, Any]] = None,
        include_disclaimer: bool = False,
    ) -> bool:
        """Legacy webhook send path.

        Args:
            content: 消息内容
            timeout_seconds: 请求超时时间
            render_context: 渲染上下文，用于结构化卡片渲染
            include_disclaimer: 分析报告推送为 True，页脚补统一声明
        """
        formatted_content = format_feishu_markdown(content)

        max_bytes = self._feishu_max_bytes
        keyword_overhead = len(self._get_keyword_prefix().encode("utf-8"))
        effective_max_bytes = max_bytes - keyword_overhead - _WEBHOOK_ENVELOPE_RESERVE_BYTES

        if effective_max_bytes <= 0:
            logger.error("飞书关键词过长，超过单条消息允许的最大字节数，无法发送")
            return False

        content_bytes = len(formatted_content.encode("utf-8")) + keyword_overhead
        if content_bytes > max_bytes - _WEBHOOK_ENVELOPE_RESERVE_BYTES:
            min_chunk_bytes = MIN_MAX_BYTES + PAGE_MARKER_SAFE_BYTES
            if effective_max_bytes < min_chunk_bytes:
                logger.error(
                    "飞书关键词过长，剩余分片预算(%s字节)不足以安全分页发送，至少需要 %s 字节",
                    effective_max_bytes,
                    min_chunk_bytes,
                )
                return False
            logger.info("飞书消息内容超长(%d字节/%d字符)，将分批发送", content_bytes, len(content))
            return self._send_feishu_chunked(
                formatted_content,
                effective_max_bytes,
                include_disclaimer=include_disclaimer,
                render_context=render_context,
            )

        try:
            return self._send_feishu_message(
                formatted_content,
                timeout_seconds=timeout_seconds,
                include_disclaimer=include_disclaimer,
                render_context=render_context,
            )
        except Exception as e:
            logger.error("发送飞书消息失败: %s", e)
            return False

    def _send_feishu_chunked(
        self,
        content: str,
        max_bytes: int,
        *,
        include_disclaimer: bool = False,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """分片发送超长内容，续页继承标题并加页码。

        改进点（F1 热修）：
        - 提取原始标题，续页在开头加上「标题 (N/M)」
        - 避免续页因缺少一级标题而回退为通用标题

        结构化渲染上下文只作用于首片，避免每片重复渲染信号区。
        """
        # 提取原始一级标题
        title = self._extract_h1_title(content)

        try:
            chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        except ValueError as e:
            logger.error("飞书消息分片失败，单片预算不足以安全分页（关键词过长或 max_bytes 过小）: %s", e)
            return False

        total_chunks = len(chunks)
        success_count = 0
        logger.info("飞书分批发送：共 %d 批", total_chunks)
        for i, chunk in enumerate(chunks):
            try:
                # 续页继承标题并加页码
                if i > 0 and title:
                    page_header = f"# {title} ({i + 1}/{total_chunks})\n\n"
                    chunk = page_header + chunk

                if self._send_feishu_message(
                    chunk,
                    include_disclaimer=include_disclaimer,
                    render_context=render_context if i == 0 else None,
                ):
                    success_count += 1
                    logger.info("飞书第 %d/%d 批发送成功", i + 1, total_chunks)
                else:
                    logger.error("飞书第 %d/%d 批发送失败", i + 1, total_chunks)
            except Exception as e:
                logger.error("飞书第 %d/%d 批发送异常: %s", i + 1, total_chunks, e)
            if i < total_chunks - 1:
                time.sleep(1)
        return success_count == total_chunks

    @staticmethod
    def _extract_h1_title(content: str) -> str:
        """从内容中提取一级标题（# 开头）。"""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                # 去掉 # 前缀和可能的 emoji
                title = stripped[2:].strip()
                # 去掉 lark_md 格式后的前缀符号
                title = title.lstrip("▎·").strip()
                return title
        return ""

    def _send_feishu_message(
        self,
        content: str,
        *,
        timeout_seconds: Optional[float] = None,
        include_disclaimer: bool = False,
        render_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a single Feishu webhook message (interactive card, fallback text).

        改进点（F1 热修）：
        1. 使用实际请求体大小计量，而不是 UTF-8 字节数
        2. 纯文本兜底时去除 lark_md 标记
        3. 限流错误时重试
        4. 关键词不参与标题解析，而是添加到页脚
        5. 结构化上下文只用于卡片信号区，不影响纯文本兜底
        """
        security_fields = self._build_security_fields()

        def _post_payload_with_retry(payload: Dict[str, Any]) -> bool:
            """发送请求体，支持限流重试。"""
            request_payload = dict(payload)
            request_payload.update(security_fields)

            # 按真实发送口径（UTF-8 JSON）检查请求体大小；超限时不发，
            # 避免必然被飞书拒绝后把错误当成“网络失败”重试。
            try:
                request_body = _encode_webhook_request_body(request_payload)
            except (TypeError, ValueError) as e:
                logger.error("飞书请求体序列化失败: %s", e)
                return False
            actual_size = len(request_body)
            if actual_size > self._feishu_max_bytes:
                logger.warning(
                    "飞书请求体超限: %d 字节 > %d 字节限制，跳过发送",
                    actual_size,
                    self._feishu_max_bytes,
                )
                return False

            for attempt in range(_WEBHOOK_RATE_LIMIT_RETRIES + 1):
                try:
                    response = requests.post(
                        self._feishu_url,
                        data=request_body,
                        headers={"Content-Type": "application/json; charset=utf-8"},
                        timeout=timeout_seconds or _WEBHOOK_SEND_TIMEOUT_SECONDS,
                        verify=self._webhook_verify_ssl,
                    )
                except (requests.exceptions.ConnectionError,
                         requests.exceptions.Timeout,
                         requests.exceptions.RequestException) as e:
                    logger.error("飞书 Webhook 网络请求异常: %s", e)
                    return False

                if response.status_code == 200:
                    try:
                        result = response.json()
                    except (ValueError, AttributeError):
                        logger.error("飞书 Webhook 返回非 JSON 响应: %s", response.text[:200])
                        return False
                    if not isinstance(result, dict):
                        logger.error("飞书 Webhook 返回非预期格式: %s", type(result).__name__)
                        return False
                    code = result.get("code") if "code" in result else result.get("StatusCode")
                    if code == 0:
                        logger.info("飞书 Webhook 消息发送成功")
                        return True
                    # 检查是否为限流错误
                    if code in _WEBHOOK_RATE_LIMIT_ERROR_CODES and attempt < _WEBHOOK_RATE_LIMIT_RETRIES:
                        backoff = _WEBHOOK_RATE_LIMIT_BACKOFF_SECONDS[
                            min(attempt, len(_WEBHOOK_RATE_LIMIT_BACKOFF_SECONDS) - 1)
                        ]
                        logger.warning(
                            "飞书 Webhook 限流 [code=%s]，%0.1f 秒后重试 (%d/%d)",
                            code,
                            backoff,
                            attempt + 1,
                            _WEBHOOK_RATE_LIMIT_RETRIES,
                        )
                        time.sleep(backoff)
                        continue
                    logger.error(
                        "飞书 Webhook 返回错误 [code=%s]: %s",
                        code,
                        result.get("msg") or result.get("StatusMessage", "未知错误"),
                    )
                    return False
                elif response.status_code == 429 and attempt < _WEBHOOK_RATE_LIMIT_RETRIES:
                    # HTTP 429 Too Many Requests
                    backoff = _WEBHOOK_RATE_LIMIT_BACKOFF_SECONDS[
                        min(attempt, len(_WEBHOOK_RATE_LIMIT_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        "飞书 Webhook HTTP 429 限流，%0.1f 秒后重试 (%d/%d)",
                        backoff,
                        attempt + 1,
                        _WEBHOOK_RATE_LIMIT_RETRIES,
                    )
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("飞书 Webhook 请求失败: HTTP %d", response.status_code)
                    return False
            return False

        # 尝试发送卡片（不带关键词前缀，关键词稍后添加到页脚）
        card_body = self._build_card_body(
            content,
            max_bytes=max(1, self._feishu_max_bytes - _WEBHOOK_ENVELOPE_RESERVE_BYTES),
            include_disclaimer=include_disclaimer,
            render_context=render_context,
        )
        
        if card_body is not None:
            # 如果有配置关键词，添加到卡片的页脚
            if self._feishu_keyword:
                self._add_keyword_to_card_footer(card_body, self._feishu_keyword)
            
            card_payload = {
                "msg_type": "interactive",
                "card": card_body,
            }

            if _post_payload_with_retry(card_payload):
                return True

        # 卡片失败或超限，降级为纯文本（去除标记，带关键词前缀）
        text_content = ensure_report_disclaimer(content) if include_disclaimer else content
        prepared_content = self._apply_keyword_prefix(text_content)
        plain_text = strip_feishu_markup(prepared_content)
        text_payload = {
            "msg_type": "text",
            "content": {"text": plain_text},
        }
        return _post_payload_with_retry(text_payload)

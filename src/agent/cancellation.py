# -*- coding: utf-8 -*-
"""T26: 运行级取消事件注册表。

前端“停止”以前只是断开 SSE 连接，后端线程会继续跑完所有模型调用与工具并落库，
既消耗 token，也让下一轮与上一轮并发。这里按 ``run_id``（即客户端 request_id）
注册一个 ``threading.Event``：

- ``POST /api/v1/agent/chat/stop`` 置位；
- SSE 生成器因客户端断开结束时置位；
- ``run_agent_loop`` 在每个检查点读取，退出后由生成器 ``release_run`` 回收。

设计上刻意保持进程内、无持久化：事件只在一次运行期间有意义，进程重启后
运行态本身会被 ``_CHAT_RUNNING_STALE_AFTER`` 清理。
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

_lock = threading.Lock()
_events: Dict[str, threading.Event] = {}


def register_run(run_id: str) -> threading.Event:
    """Register (or replace) the cancel event for ``run_id``."""
    key = str(run_id or "").strip()
    event = threading.Event()
    if not key:
        return event
    with _lock:
        _events[key] = event
    return event


def get_run_event(run_id: str) -> Optional[threading.Event]:
    """Return the cancel event for ``run_id`` if it is still registered."""
    key = str(run_id or "").strip()
    if not key:
        return None
    with _lock:
        return _events.get(key)


def request_cancel(run_id: str) -> bool:
    """Set the cancel event. Returns False when ``run_id`` is unknown."""
    event = get_run_event(run_id)
    if event is None:
        return False
    event.set()
    return True


def is_cancelled(run_id: str) -> bool:
    event = get_run_event(run_id)
    return bool(event is not None and event.is_set())


def release_run(run_id: str) -> None:
    """Drop the registration once the run has produced its terminal event."""
    key = str(run_id or "").strip()
    if not key:
        return
    with _lock:
        _events.pop(key, None)


__all__ = [
    "register_run",
    "get_run_event",
    "request_cancel",
    "is_cancelled",
    "release_run",
]

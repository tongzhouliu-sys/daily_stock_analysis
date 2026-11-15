# -*- coding: utf-8 -*-
"""协作式取消信号。

任务队列在进度检查点抛这个异常来中止正在运行的分析。它必须穿过管线与
分析器里那些"进度回调失败不影响主流程"的 ``except Exception``，否则取消
只会在任务跑完、落库、发通知之后才生效。

放在 ``src/core`` 而不是任务队列模块里，避免 ``src/analyzer.py`` 为了捕获
取消信号而依赖服务层。
"""

from __future__ import annotations


class TaskCancelledError(Exception):
    """Raised inside a running task when the user requested cancellation."""

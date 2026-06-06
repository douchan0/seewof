"""本地日志 + 异步上传队列.

- 写文件: rotating file, 按天切分
- 内存环形缓冲: 最多 2000 条, 用于上传给管理端
- 线程安全, 大量使用环境: 单写单读, 锁开销可忽略
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


_MAX_RING = 2000
_RING: deque[dict[str, Any]] = deque(maxlen=_MAX_RING)
_RING_LOCK = threading.Lock()
_HANDLER_INSTALLED = False


def setup_logger(log_dir: str, name: str = "seewof") -> logging.Logger:
    """配置根 logger, 重复调用幂等."""
    global _HANDLER_INSTALLED
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"{name}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if _HANDLER_INSTALLED:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 文件 - 按天切分, 保留 14 天
    file_h = TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=14, encoding="utf-8",
    )
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    # 控制台 - 仅 DEBUG 模式
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.WARNING)
    logger.addHandler(console)

    _HANDLER_INSTALLED = True
    return logger


# ---------------------------------------------------------------------------
# 结构化事件
# ---------------------------------------------------------------------------
def log_event(
    logger: logging.Logger,
    event: str,
    *,
    source: str | None = None,
    detail: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """记录结构化事件, 同时写入日志文件 + 内存环形缓冲."""
    payload = {
        "t": int(time.time()),
        "event": event,
        "source": source or "",
        "detail": detail or {},
    }
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    logger.log(level, line)
    with _RING_LOCK:
        _RING.append(payload)


def drain_ring(limit: int = 200) -> list[dict[str, Any]]:
    """读取并清空环形缓冲 (上传后调用)."""
    with _RING_LOCK:
        items = list(_RING)
        _RING.clear()
    return items[-limit:]


def peek_ring(limit: int = 100) -> list[dict[str, Any]]:
    with _RING_LOCK:
        items = list(_RING)
    return items[-limit:]

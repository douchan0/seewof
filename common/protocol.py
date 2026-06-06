"""共享协议 - 消息格式定义.

教室端 <-> 管理端 通信使用 JSON over HTTPS, 所有 body 走 HMAC 签名.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 事件类型 (教室端 -> 管理端)
# ---------------------------------------------------------------------------
class EventType(str, Enum):
    STARTUP = "startup"                  # 服务启动
    SHUTDOWN = "shutdown"                # 服务停止
    HEARTBEAT = "heartbeat"              # 心跳 (含时钟同步)
    LOCK = "lock"                        # 锁定状态变化
    UNLOCK = "unlock"                    # 解锁状态变化
    USB_INSERT = "usb_insert"            # U盘插入
    USB_REMOVE = "usb_remove"            # U盘拔出
    USB_VERIFY_OK = "usb_verify_ok"      # U盘验证成功
    USB_VERIFY_FAIL = "usb_verify_fail"  # U盘验证失败
    NET_LOST = "net_lost"                # 网络断开
    NET_RECOVER = "net_recover"          # 网络恢复
    TIME_DRIFT = "time_drift"            # 时钟漂移警告
    WATCHDOG_RESTART = "watchdog_restart"  # 守护重启


class UnlockSource(str, Enum):
    USB = "usb"                          # 第一优先级
    SCHEDULE = "schedule"                # 第二优先级
    REMOTE = "remote"                    # 第三优先级
    BOOT = "boot"                        # 启动时短暂解锁 (安装/维护)


# ---------------------------------------------------------------------------
# 事件 payload
# ---------------------------------------------------------------------------
@dataclass
class Event:
    event: str                              # EventType 值
    classroom_id: str                       # 教室标识
    server_ts: int                          # 管理端时间戳 (由管理端在收到时填充)
    agent_ts: int                           # 教室端本地时间戳
    source: str | None = None               # 解锁/锁定来源 (UnlockSource)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes | str) -> "Event":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        obj = json.loads(data)
        return cls(
            event=obj["event"],
            classroom_id=obj["classroom_id"],
            server_ts=obj.get("server_ts", 0),
            agent_ts=obj.get("agent_ts", 0),
            source=obj.get("source"),
            detail=obj.get("detail", {}) or {},
        )


@dataclass
class Heartbeat(Event):
    """心跳, 携带教室端状态摘要."""

    locked: bool = True
    has_usb: bool = False
    time_drift_sec: int = 0
    uptime_sec: int = 0
    pending_remote_unlock: int = 0  # 剩余秒数, 0 表示无

    def to_json(self) -> bytes:
        base = json.loads(super().to_json())
        base.update({
            "locked": self.locked,
            "has_usb": self.has_usb,
            "time_drift_sec": self.time_drift_sec,
            "uptime_sec": self.uptime_sec,
            "pending_remote_unlock": self.pending_remote_unlock,
        })
        return json.dumps(base, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# 控制指令 (管理端 -> 教室端)
# ---------------------------------------------------------------------------
@dataclass
class UnlockCommand:
    """远程解锁指令."""

    command_id: str
    duration_sec: int                 # 解锁持续秒数
    issued_at: int                    # 管理端签发时间
    issued_by: str                    # 操作者 (教师账号)
    reason: str = ""

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes | str) -> "UnlockCommand":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        obj = json.loads(data)
        return cls(**obj)

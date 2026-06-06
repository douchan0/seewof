"""Pydantic schemas (请求/响应模型)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
class Ok(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str


# ---------------------------------------------------------------------------
# 教室
# ---------------------------------------------------------------------------
class ClassroomIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str
    mac: str = ""
    ip: str = ""
    psk: str = Field(min_length=16, max_length=128)


class ClassroomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    mac: str
    ip: str
    online: bool
    locked: bool
    current_state: str
    last_seen: datetime | None = None
    has_usb: bool = False
    time_drift_sec: int = 0
    pending_remote_unlock: int = 0


# ---------------------------------------------------------------------------
# 时间表
# ---------------------------------------------------------------------------
class TimeSlot(BaseModel):
    weekdays: list[int] = Field(min_length=1, max_length=7,
                                description="0=Mon ... 6=Sun")
    start_min: int = Field(ge=0, le=1440)
    end_min: int = Field(ge=0, le=1440)


class ScheduleIn(BaseModel):
    name: str = "default"
    slots: list[TimeSlot]


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slots: list[dict[str, Any]]
    updated_at: datetime


# ---------------------------------------------------------------------------
# 远程指令
# ---------------------------------------------------------------------------
class RemoteUnlockIn(BaseModel):
    duration_sec: int = Field(ge=10, le=86400)
    reason: str = ""


class RemoteUnlockOut(BaseModel):
    command_id: str
    classroom_id: str
    duration_sec: int
    expires_at: datetime
    issued_by: str
    reason: str


# ---------------------------------------------------------------------------
# U 盘
# ---------------------------------------------------------------------------
class UsbKeyIn(BaseModel):
    serial: str = Field(min_length=4)
    teacher_id: str
    teacher_name: str
    expires_at: datetime | None = None


class UsbKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    serial: str
    teacher_id: str
    teacher_name: str
    issued_at: datetime
    expires_at: datetime | None = None
    revoked: bool
    last_seen: datetime | None = None


# ---------------------------------------------------------------------------
# 事件 / 日志
# ---------------------------------------------------------------------------
class EventIn(BaseModel):
    classroom: str
    event: str
    source: str = ""
    agent_ts: int
    detail: dict[str, Any] = Field(default_factory=dict)


class LogBatchIn(BaseModel):
    classroom: str
    items: list[EventIn]


class LogItem(BaseModel):
    id: int
    classroom_id: str
    event: str
    source: str
    agent_ts: datetime
    server_ts: datetime
    detail: dict[str, Any]


class LogPage(BaseModel):
    total: int
    items: list[LogItem]


# ---------------------------------------------------------------------------
# Poll 响应 (管理端 -> 教室端)
# ---------------------------------------------------------------------------
class PollOut(BaseModel):
    server_ts: int
    slots: list[dict[str, Any]]
    remote_unlock: dict[str, Any] | None
    drift_sec: int = 0


LoginOut.model_rebuild()

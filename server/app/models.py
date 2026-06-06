"""ORM 模型."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Integer, String, Text, DateTime, Boolean, ForeignKey, Float, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# 教师用户
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="teacher")  # teacher | admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ---------------------------------------------------------------------------
# 教室
# ---------------------------------------------------------------------------
class Classroom(Base):
    __tablename__ = "classrooms"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    mac: Mapped[str] = mapped_column(String(32), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    psk: Mapped[str] = mapped_column(String(128))          # 预共享密钥
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=True)
    current_state: Mapped[str] = mapped_column(String(32), default="unknown")

    schedules: Mapped[list["Schedule"]] = relationship(back_populates="classroom", cascade="all, delete-orphan")
    commands: Mapped[list["RemoteCommand"]] = relationship(back_populates="classroom", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 时间表 (按教室分组)
# ---------------------------------------------------------------------------
class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    classroom_id: Mapped[str] = mapped_column(String(64), ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="default")
    # JSON: [{"weekdays":[0,1,2,3,4], "start_min":480, "end_min":720}, ...]
    slots_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    classroom: Mapped[Classroom] = relationship(back_populates="schedules")

    def slots(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.slots_json or "[]")
        except json.JSONDecodeError:
            return []

    def set_slots(self, slots: list[dict[str, Any]]) -> None:
        self.slots_json = json.dumps(slots, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 远程解锁指令 (短期)
# ---------------------------------------------------------------------------
class RemoteCommand(Base):
    __tablename__ = "remote_commands"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classroom_id: Mapped[str] = mapped_column(String(64), ForeignKey("classrooms.id", ondelete="CASCADE"), index=True)
    command_type: Mapped[str] = mapped_column(String(16), default="unlock")
    duration_sec: Mapped[int] = mapped_column(Integer)
    issued_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(256), default="")
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)

    classroom: Mapped[Classroom] = relationship(back_populates="commands")


# ---------------------------------------------------------------------------
# U 盘授权
# ---------------------------------------------------------------------------
class UsbKey(Base):
    __tablename__ = "usb_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    teacher_id: Mapped[str] = mapped_column(String(64))
    teacher_name: Mapped[str] = mapped_column(String(128))
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# 事件日志 (来自教室端)
# ---------------------------------------------------------------------------
class EventLog(Base):
    __tablename__ = "event_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    classroom_id: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="")
    agent_ts: Mapped[datetime] = mapped_column(DateTime)
    server_ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (
        Index("ix_event_classroom_time", "classroom_id", "server_ts"),
    )

    def detail(self) -> dict[str, Any]:
        try:
            return json.loads(self.detail_json or "{}")
        except json.JSONDecodeError:
            return {}


# ---------------------------------------------------------------------------
# 教室最新状态缓存 (来自 heartbeat)
# ---------------------------------------------------------------------------
class ClassroomState(Base):
    __tablename__ = "classroom_state"
    classroom_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=True)
    has_usb: Mapped[bool] = mapped_column(Boolean, default=False)
    time_drift_sec: Mapped[int] = mapped_column(Integer, default=0)
    uptime_sec: Mapped[int] = mapped_column(Integer, default=0)
    pending_remote_unlock: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

"""决策状态机.

输入: 三个解锁信号
- USB 插入/拔出 (带验证结果)
- 时段查询 (来自管理端时间表)
- 远程指令 (倒计时)

输出: 锁定 / 解锁 状态, 以及触发原因.

严格优先级: USB > SCHEDULE > REMOTE.

实现: 纯函数决策 + 状态机, 无副作用, 便于单元测试.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class LockState(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"


class Reason(str, Enum):
    USB = "usb"
    SCHEDULE = "schedule"
    REMOTE = "remote"
    INITIAL = "initial"


@dataclass
class Schedule:
    """当前时段 (由管理端下发的当日计划计算得出)."""

    in_session: bool
    seconds_to_end: int = 0         # 距结束秒数, -1 表示无时段
    next_start_in_sec: int = -1     # 距下次开始秒数, -1 表示无


@dataclass
class RemoteGrant:
    """远程解锁授予."""

    expires_at: int = 0             # unix 秒, 0 表示无
    command_id: str = ""

    @property
    def active(self) -> bool:
        return self.expires_at > int(time.time())

    def seconds_left(self) -> int:
        return max(0, self.expires_at - int(time.time()))


@dataclass
class Context:
    has_valid_usb: bool = False
    schedule: Schedule = field(default_factory=lambda: Schedule(in_session=False))
    remote: RemoteGrant = field(default_factory=RemoteGrant)
    soft_warn_sec: int = 30

    def in_soft_warn(self) -> bool:
        return (
            self.schedule.in_session
            and 0 < self.schedule.seconds_to_end <= self.soft_warn_sec
        )


@dataclass
class Decision:
    state: LockState
    reason: Reason
    soft_warn: bool = False
    detail: str = ""


def decide(ctx: Context) -> Decision:
    """根据当前上下文决定锁定状态.

    产品原则 (教学秩序第一):
      - 合法 U 盘是"通行证", 只要插着, 任何情况下都解锁.
        (即使时间同步失败/网络断/时段错乱, 都不能影响 USB 验证通过的课堂)
      - 时段 / 远程授权是"软"信号, 网络异常时应保持上次成功状态,
        不应 fail-secure 地把屏幕锁掉.

    优先级 (严格):
      1. 合法U盘   -> 立即解锁 (USB 优先, 凌驾一切)
      2. 时段内    -> 解锁
      3. 远程授权  -> 解锁
      4. 其他      -> 锁定
    """
    # 1. U 盘优先 (最高优先级, 是"通行证")
    if ctx.has_valid_usb:
        return Decision(LockState.UNLOCKED, Reason.USB)

    # 2. 时段
    if ctx.schedule.in_session:
        soft = ctx.in_soft_warn()
        return Decision(LockState.UNLOCKED, Reason.SCHEDULE, soft_warn=soft)

    # 3. 远程指令
    if ctx.remote.active:
        return Decision(LockState.UNLOCKED, Reason.REMOTE)

    return Decision(LockState.LOCKED, Reason.INITIAL)


# ---------------------------------------------------------------------------
# 状态机: 带事件历史的可观察对象
# ---------------------------------------------------------------------------
class StateMachine:
    """封装当前状态 + 决策历史, 用于 UI 展示和事件触发."""

    def __init__(self) -> None:
        self._state: LockState = LockState.LOCKED
        self._reason: Reason = Reason.INITIAL
        self._last_change_ts: int = 0
        self._history: list[tuple[int, LockState, Reason]] = []

    @property
    def state(self) -> LockState:
        return self._state

    @property
    def reason(self) -> Reason:
        return self._reason

    def update(self, ctx: Context) -> tuple[Decision, bool]:
        """更新状态, 返回 (decision, changed?)."""
        d = decide(ctx)
        changed = d.state != self._state or d.reason != self._reason
        if changed:
            self._state = d.state
            self._reason = d.reason
            self._last_change_ts = int(time.time())
            self._history.append((self._last_change_ts, d.state, d.reason))
            # 限制历史长度
            if len(self._history) > 200:
                self._history = self._history[-200:]
        return d, changed

    def history(self) -> Iterable[tuple[int, LockState, Reason]]:
        return tuple(self._history)


# ---------------------------------------------------------------------------
# 时段计算 (管理端下发的当日 + 跨日)
# ---------------------------------------------------------------------------
@dataclass
class TimeSlot:
    """单个时段: 每天 minute_of_day_start..minute_of_day_end."""

    weekdays: tuple[int, ...]      # 0=Mon ... 6=Sun
    start_min: int                 # 0..1440
    end_min: int                   # 0..1440, 可 < start_min 表示跨夜


def evaluate_schedule(
    slots: list[TimeSlot],
    *,
    now_epoch: int,
    soft_warn_sec: int = 30,
) -> Schedule:
    """根据管理端时间计算当前是否在某个时段内.

    now_epoch 使用管理端同步过来的时间, 严禁使用本地时间.
    """
    import datetime as _dt

    if not slots:
        return Schedule(in_session=False)

    now = _dt.datetime.fromtimestamp(now_epoch)
    weekday = now.weekday()
    minute = now.hour * 60 + now.minute
    second = now.second

    in_session = False
    seconds_to_end = 0
    earliest_next_start: int | None = None

    for slot in slots:
        if weekday not in slot.weekdays:
            continue
        s, e = slot.start_min, slot.end_min
        if s < e:
            # 当日时段
            if s <= minute < e:
                in_session = True
                seconds_to_end = (e - minute) * 60 - second
                break
            # 计算到下次开始的秒数
            if minute < s:
                delta = (s - minute) * 60 - second
            else:
                # 明天
                delta = ((24 * 60 - minute) + s) * 60 - second
        else:
            # 跨夜时段
            if minute >= s or minute < e:
                in_session = True
                if minute >= s:
                    seconds_to_end = ((24 * 60 - minute) + e) * 60 - second
                else:
                    seconds_to_end = (e - minute) * 60 - second
                break
            if minute < s:
                delta = (s - minute) * 60 - second
            else:
                delta = ((24 * 60 - minute) + s) * 60 - second
        if earliest_next_start is None or delta < earliest_next_start:
            earliest_next_start = delta

    return Schedule(
        in_session=in_session,
        seconds_to_end=max(0, seconds_to_end) if in_session else 0,
        next_start_in_sec=earliest_next_start if earliest_next_start is not None else -1,
    )

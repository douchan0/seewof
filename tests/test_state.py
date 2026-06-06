"""决策状态机 + 时段计算测试."""

from __future__ import annotations

import time
import unittest

from agent.state import (
    Context, Decision, LockState, Reason, RemoteGrant, Schedule,
    StateMachine, TimeSlot, decide, evaluate_schedule,
)


class DecideTest(unittest.TestCase):
    def test_usb_priority_over_schedule(self):
        ctx = Context(has_valid_usb=True,
                      schedule=Schedule(in_session=False),
                      remote=RemoteGrant())
        d = decide(ctx)
        self.assertEqual(d.state, LockState.UNLOCKED)
        self.assertEqual(d.reason, Reason.USB)

    def test_usb_priority_over_remote(self):
        ctx = Context(has_valid_usb=True,
                      schedule=Schedule(in_session=False),
                      remote=RemoteGrant(expires_at=int(time.time()) + 1000))
        d = decide(ctx)
        self.assertEqual(d.state, LockState.UNLOCKED)
        self.assertEqual(d.reason, Reason.USB)

    def test_schedule_when_no_usb(self):
        ctx = Context(has_valid_usb=False,
                      schedule=Schedule(in_session=True, seconds_to_end=600),
                      remote=RemoteGrant())
        d = decide(ctx)
        self.assertEqual(d.state, LockState.UNLOCKED)
        self.assertEqual(d.reason, Reason.SCHEDULE)
        self.assertFalse(d.soft_warn)

    def test_schedule_soft_warn(self):
        ctx = Context(has_valid_usb=False,
                      schedule=Schedule(in_session=True, seconds_to_end=20),
                      remote=RemoteGrant(),
                      soft_warn_sec=30)
        d = decide(ctx)
        self.assertEqual(d.state, LockState.UNLOCKED)
        self.assertTrue(d.soft_warn)

    def test_remote_when_no_usb_no_schedule(self):
        ctx = Context(has_valid_usb=False,
                      schedule=Schedule(in_session=False),
                      remote=RemoteGrant(expires_at=int(time.time()) + 1000))
        d = decide(ctx)
        self.assertEqual(d.state, LockState.UNLOCKED)
        self.assertEqual(d.reason, Reason.REMOTE)

    def test_lock_default(self):
        ctx = Context(has_valid_usb=False,
                      schedule=Schedule(in_session=False),
                      remote=RemoteGrant())
        d = decide(ctx)
        self.assertEqual(d.state, LockState.LOCKED)
        self.assertEqual(d.reason, Reason.INITIAL)


class EvaluateScheduleTest(unittest.TestCase):
    """时段计算: 用已知 epoch 时间测试."""

    def _epoch(self, y, m, d, H, M):
        import datetime as _dt
        return int(_dt.datetime(y, m, d, H, M).timestamp())

    def test_in_session(self):
        # 2024-01-01 是周一
        slots = [TimeSlot(weekdays=(0, 1, 2, 3, 4),
                          start_min=8 * 60, end_min=12 * 60)]
        # 周一 9:00
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 1, 9, 0))
        self.assertTrue(s.in_session)
        # 周一 13:00
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 1, 13, 0))
        self.assertFalse(s.in_session)
        # 周日 9:00
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 7, 9, 0))
        self.assertFalse(s.in_session)

    def test_cross_midnight(self):
        # 周三 20:00-02:00 (跨夜)
        slots = [TimeSlot(weekdays=(2,),
                          start_min=20 * 60, end_min=2 * 60)]
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 3, 23, 0))
        self.assertTrue(s.in_session)
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 3, 1, 0))
        self.assertTrue(s.in_session)
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 3, 19, 0))
        self.assertFalse(s.in_session)
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 3, 3, 0))
        self.assertFalse(s.in_session)

    def test_seconds_to_end(self):
        slots = [TimeSlot(weekdays=(0, 1, 2, 3, 4, 5, 6),
                          start_min=8 * 60, end_min=10 * 60)]
        s = evaluate_schedule(slots, now_epoch=self._epoch(2024, 1, 1, 9, 30))
        self.assertTrue(s.in_session)
        self.assertEqual(s.seconds_to_end, 30 * 60)


class StateMachineTest(unittest.TestCase):
    def test_change_event(self):
        sm = StateMachine()
        # 初始为 LOCKED, 第一次传入 LOCK 上下文不算 changed (同状态)
        ctx = Context(has_valid_usb=False,
                      schedule=Schedule(in_session=False))
        d, changed = sm.update(ctx)
        self.assertFalse(changed)
        self.assertEqual(sm.state, LockState.LOCKED)

        # 切到 UNLOCK 算 changed
        ctx.has_valid_usb = True
        d, changed = sm.update(ctx)
        self.assertTrue(changed)
        self.assertEqual(sm.state, LockState.UNLOCKED)
        self.assertEqual(sm.reason, Reason.USB)

        # 再次相同状态: not changed
        d, changed = sm.update(ctx)
        self.assertFalse(changed)

        # 切回 LOCK 算 changed
        ctx.has_valid_usb = False
        d, changed = sm.update(ctx)
        self.assertTrue(changed)
        self.assertEqual(sm.state, LockState.LOCKED)


if __name__ == "__main__":
    unittest.main()

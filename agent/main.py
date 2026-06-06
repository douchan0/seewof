"""主服务入口.

线程模型:
- main: 协调者, 决策, 状态机
- USB monitor (UsbMonitor): 1 个线程
- 心跳 + 日志上传: 1 个线程
- 输入拦截钩子: 系统注入 (非自有线程)
- 遮罩: 1 个线程 (Qt)
- 时段计算 + 决策: main 线程定时

主进程和 watchdog 互相监视, 任一被结束则另一方立即重启.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from common.protocol import Event, EventType, UnlockSource

from .config import AgentConfig, ConfigError
from .comm import ServerClient
from .input_blocker import InputBlocker, TouchBlocker
from .logger import log_event, setup_logger
from .overlay import Overlay
from .protection import Protection
from .state import Context, Reason, StateMachine, evaluate_schedule
from .usbmgr import UsbEvent, UsbMonitor, verify_teacher_key


class Agent:
    """主服务."""

    def __init__(self, cfg: AgentConfig) -> None:
        self._cfg = cfg
        self._log = setup_logger(cfg.log_dir, "seewof")
        self._log.info("agent starting classroom_id=%s", cfg.classroom_id)

        # 加载公钥
        pub_pem = self._load_public_key()

        # 子系统
        self._server = ServerClient(cfg.server, cfg.classroom_id)
        self._state = StateMachine()
        self._usb = UsbMonitor(
            cfg.usb, pub_pem,
            on_insert=self._on_usb_insert,
            on_remove=self._on_usb_remove,
        )
        self._blocker = InputBlocker(cfg.lock, on_hotkey=self._on_hotkey)
        self._touch = TouchBlocker()
        self._protection = Protection()
        self._overlay = Overlay(cfg.lock)

        # 当前上下文
        self._ctx = Context(soft_warn_sec=cfg.lock.schedule_soft_warn_sec)
        self._remote_lock = threading.Lock()
        self._time_slots: list = []  # 来自管理端
        self._pending_remote_until = 0
        self._pending_remote_id = ""
        self._usb_remove_deadline: float = 0.0  # 拔盘后延迟锁定的截止时间
        self._running = False
        self._start_ts = int(time.time())

    def _load_public_key(self) -> bytes:
        path = self._cfg.usb.public_key_path
        if not path or not Path(path).exists():
            self._log.warning("public key not found at %s; U盘验证将全部失败", path)
            return b""
        return Path(path).read_bytes()

    # ------------------------------------------------------------------ pub
    def run(self) -> int:
        self._running = True
        self._install_signal_handlers()

        # 启动顺序很重要
        try:
            self._overlay.start()
        except Exception as e:
            self._log.error("overlay start failed: %s", e)
        try:
            self._blocker.start()
        except OSError as e:
            self._log.error("input_blocker start failed: %s", e)
        self._touch.set_blocked(True)
        self._protection.apply()
        self._usb.start()

        log_event(self._log, EventType.STARTUP.value,
                  detail={"pid": os.getpid()})

        # 启动协调线程
        threading.Thread(target=self._heartbeat_loop, name="Heartbeat", daemon=True).start()
        threading.Thread(target=self._decision_loop, name="Decision", daemon=True).start()
        threading.Thread(target=self._log_upload_loop, name="LogUploader", daemon=True).start()

        # 主线程空转
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self._log.info("KeyboardInterrupt")
        return 0

    def stop(self) -> None:
        self._log.info("agent stopping")
        self._running = False
        try:
            self._protection.revert()
        except Exception as e:
            self._log.warning("protection.revert: %s", e)
        try:
            self._blocker.stop()
        except Exception as e:
            self._log.warning("blocker.stop: %s", e)
        try:
            self._touch.set_blocked(False)
        except Exception as e:
            self._log.warning("touch.unblock: %s", e)
        try:
            self._usb.stop()
        except Exception as e:
            self._log.warning("usb.stop: %s", e)
        try:
            self._overlay.stop()
        except Exception as e:
            self._log.warning("overlay.stop: %s", e)
        log_event(self._log, EventType.SHUTDOWN.value)

    # ---------------------------------------------------------- 协调
    def _decision_loop(self) -> None:
        """每秒根据上下文重新决策."""
        while self._running:
            now = int(time.time())
            # 1. 处理 U 盘拔出的延迟锁定
            if self._ctx.has_valid_usb is False and self._usb_remove_deadline > 0:
                if time.time() >= self._usb_remove_deadline:
                    self._usb_remove_deadline = 0
                    # 通知一次
                    self._log.info("usb remove grace period elapsed")

            # 2. 计算时段
            server_now = self._server.clock().now() or now
            self._ctx.schedule = evaluate_schedule(
                self._time_slots, now_epoch=server_now,
                soft_warn_sec=self._cfg.lock.schedule_soft_warn_sec,
            )
            # 3. 远程授权
            with self._remote_lock:
                self._ctx.remote.expires_at = self._pending_remote_until
                self._ctx.remote.command_id = self._pending_remote_id
            # 4. 决策
            from .state import decide
            d = decide(self._ctx) if False else None  # see below
            from .state import decide as _decide
            d = _decide(self._ctx)
            _, changed = self._state.update(self._ctx)

            # 5. 应用到子系统
            if d.state.value == "unlocked":
                self._blocker.set_locked(False)
                self._touch.set_blocked(False)
                if d.soft_warn:
                    self._overlay.show_soft_warn()
                else:
                    self._overlay.show_unlock()
            else:
                # 锁定: 触发延迟 (U盘拔出) 还是立即
                if self._ctx.has_valid_usb is False and \
                   self._usb_remove_deadline > time.time() < float("inf") and \
                   (self._ctx.schedule.in_session or self._ctx.remote.active):
                    # U 盘刚拔出, 仍在时段或远程有效, 保持解锁
                    self._blocker.set_locked(False)
                else:
                    self._blocker.set_locked(True)
                    self._touch.set_blocked(True)
                    self._overlay.show_lock()

            if changed:
                ev = EventType.UNLOCK.value if d.state.value == "unlocked" else EventType.LOCK.value
                log_event(
                    self._log, ev,
                    source=d.reason.value,
                    detail={"soft_warn": d.soft_warn},
                )
            time.sleep(1)

    def _heartbeat_loop(self) -> None:
        """每 N 秒: 时间同步 + 拉取最新配置/指令 + 报告状态."""
        next_sync = 0.0
        next_poll = 0.0
        while self._running:
            now = time.time()
            if now >= next_sync:
                ok = self._server.sync_time()
                if not ok:
                    self._log.warning("time sync failed (failures=%d)",
                                      self._server.clock().consecutive_failures)
                # 时钟同步失败累计 3 次, 强制锁定
                if self._server.clock().consecutive_failures >= 3:
                    self._time_slots = []
                    self._log.warning("clock sync failed 3x; force schedule disabled")
                next_sync = now + self._cfg.server.time_sync_interval_sec
            if now >= next_poll:
                self._poll_server()
                next_poll = now + self._cfg.server.heartbeat_interval_sec
            time.sleep(2)

    def _poll_server(self) -> None:
        reply = self._server.fetch_poll()
        if not reply.ok:
            return
        data = reply.data
        # 更新时段
        self._time_slots = data.get("slots", [])
        # 远程指令
        cmd = data.get("remote_unlock")
        if cmd and isinstance(cmd, dict):
            try:
                with self._remote_lock:
                    self._pending_remote_until = int(cmd.get("expires_at", 0))
                    self._pending_remote_id = str(cmd.get("command_id", ""))
            except (ValueError, TypeError):
                pass
        # 时间漂移警告
        drift = data.get("drift_sec")
        if isinstance(drift, int) and abs(drift) > 60:
            log_event(self._log, EventType.TIME_DRIFT.value,
                      detail={"drift_sec": drift},
                      level=logging.WARNING)

    def _log_upload_loop(self) -> None:
        """每 10 秒上传一次日志缓冲."""
        while self._running:
            n = self._server.upload_log_ring()
            if n:
                self._log.debug("uploaded %d log items", n)
            time.sleep(10)

    # ------------------------------------------------------------ USB hooks
    def _on_usb_insert(self, ev: UsbEvent) -> None:
        if ev.valid:
            self._ctx.has_valid_usb = True
            self._usb_remove_deadline = 0
        else:
            # 非法 U 盘, 不解锁, 但也不立即切换状态 (避免给提示)
            log_event(self._log, EventType.USB_INSERT.value,
                      source=UnlockSource.USB.value,
                      detail={"drive": ev.drive, "valid": False,
                              "reason": ev.reason},
                      level=logging.WARNING)

    def _on_usb_remove(self, drive: str) -> None:
        # 重新扫描是否还有其他合法 U 盘
        valid = self._scan_any_valid_usb()
        self._ctx.has_valid_usb = valid
        if not valid:
            self._usb_remove_deadline = time.time() + self._cfg.lock.usb_remove_grace_sec

    def _scan_any_valid_usb(self) -> bool:
        import psutil
        from pathlib import Path
        pub = self._load_public_key()
        if not pub:
            return False
        for part in psutil.disk_partitions(all=False):
            d = (part.device or "")[0:1].upper()
            if not d:
                continue
            if self._cfg.usb.bind_drive_letters and d not in self._cfg.usb.bind_drive_letters:
                continue
            ev = verify_teacher_key(
                mount_root=Path(f"{d}:/"),
                drive=d, cfg=self._cfg.usb, public_key_pem=pub,
            )
            if ev.valid:
                return True
        return False

    def _on_hotkey(self, name: str) -> None:
        """Ctrl+Alt+Shift+F12 隐藏调试面板 (仅在 manage token 验证后)."""
        self._log.info("hotkey %s triggered (no-op in production)", name)

    # ----------------------------------------------------------------- priv
    def _install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGINT, lambda *_: self.stop())
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Seewof Agent")
    p.add_argument("--config", help="path to agent.json")
    p.add_argument("--service", action="store_true",
                   help="run as Windows service")
    p.add_argument("--check", action="store_true",
                   help="validate config and exit")
    args = p.parse_args()

    try:
        cfg = AgentConfig.load(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.check:
        print("config OK")
        return 0

    if args.service and os.name == "nt":
        # 交给 servicemanager
        from .service import run_as_service
        run_as_service(cfg)
        return 0

    agent = Agent(cfg)
    try:
        return agent.run()
    finally:
        agent.stop()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""MacBook 端 Mock 教室端 (跨平台, 无 Windows API 依赖).

目的: 在 MacBook 或 Linux 上完整模拟一个 seewof agent 的运行,
      验证与 seewof server 的端到端集成.

模拟的:
  ✓ HTTP 通信 (HMAC 签名)
  ✓ 时间同步 (SmoothedClock)
  ✓ 心跳 / 拉取 / 日志上传
  ✓ 状态机决策 (USB > 时段 > 远程)
  ✓ U 盘 teacher.key 验签 (旁路 WMI, 用命令行参数指定 serial)

不模拟的 (需要 Windows):
  ✗ LL 钩子键盘/鼠标拦截
  ✗ 触摸 HID 禁用
  ✗ WMI USB 枚举
  ✗ 注册表策略

用法:
  python scripts/mock_agent.py run \\
      --server http://127.0.0.1:8000 \\
      --classroom ROOM-MAC-01 \\
      --psk <psk> \\
      --serial USBSTOR-FAKE-SN-12345 \\
      --teacher-key /path/to/teacher.key

启动后, 在 stdin 输入命令:
  i    模拟 U 盘插入 (用 --serial + --teacher-key 验签)
  r    模拟 U 盘拔出
  s    打印当前状态 (locked/unlocked + 来源)
  p    立即拉取一次 server 状态 (时段+远程指令)
  t    立即时间同步
  e    上传一次日志环
  q    退出
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

# 让脚本可以直接 python scripts/mock_agent.py 跑
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent.usbmgr as usbmgr
from agent.comm import ServerClient
from agent.logger import log_event
from agent.state import Context, StateMachine, evaluate_schedule
from common.protocol import EventType, UnlockSource


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


class C:
    if _supports_color():
        RESET = "\033[0m"
        DIM = "\033[2m"
        BOLD = "\033[1m"
        RED = "\033[31m"
        GRN = "\033[32m"
        YEL = "\033[33m"
        BLU = "\033[34m"
        MAG = "\033[35m"
        CYA = "\033[36m"
    else:
        RESET = DIM = BOLD = RED = GRN = YEL = BLU = MAG = CYA = ""


def colorize(s: str, color: str) -> str:
    return f"{color}{s}{C.RESET}"


# ---------------------------------------------------------------------------
# Mock 配置
# ---------------------------------------------------------------------------
class MockConfig:
    """命令行配置."""

    def __init__(self, args: argparse.Namespace):
        self.server_url = args.server.rstrip("/")
        self.classroom_id = args.classroom
        self.psk = args.psk
        self.serial = args.serial
        self.teacher_key = args.teacher_key
        self.public_key = args.public
        self.time_sync_interval = args.time_sync_interval
        self.poll_interval = args.poll_interval
        self.log_upload_interval = args.log_upload_interval


# ---------------------------------------------------------------------------
# Mock Agent 主类
# ---------------------------------------------------------------------------
class MockAgent:
    def __init__(self, cfg: MockConfig):
        self.cfg = cfg
        self._log = self._setup_logging()
        self._server = self._make_server_client()
        self._state = StateMachine()
        self._ctx = Context(soft_warn_sec=30)
        self._time_slots: list = []
        self._remote_until: int = 0
        self._remote_id: str = ""
        self._running = False
        self._usb_inserted: bool = False
        self._command_q: Queue = Queue()
        self._start_ts = int(time.time())
        self._event_count = 0

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("mock-agent")
        logger.setLevel(logging.INFO)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            f"{C.DIM}%(asctime)s{C.RESET} %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(h)
        return logger

    def _make_server_client(self) -> ServerClient:
        # 构造 ServerConfig dataclass
        from agent.config import ServerConfig
        sc = ServerConfig(
            base_url=self.cfg.server_url,
            psk=self.cfg.psk,
            verify_tls=False,
            heartbeat_interval_sec=self.cfg.poll_interval,
            time_sync_interval_sec=self.cfg.time_sync_interval,
            request_timeout_sec=8,
        )
        return ServerClient(sc, self.cfg.classroom_id)

    # ---------------------------------------------------------------- pub
    def run(self) -> int:
        self._banner()
        # 旁路 WMI: 直接 monkey-patch get_usb_serial
        if self.cfg.serial:
            usbmgr.get_usb_serial = lambda drive, method="wmi": self.cfg.serial

        # 启动后台线程
        self._running = True
        threading.Thread(target=self._loop_time_sync, name="TimeSync", daemon=True).start()
        threading.Thread(target=self._loop_poll, name="Poll", daemon=True).start()
        threading.Thread(target=self._loop_log_upload, name="LogUp", daemon=True).start()
        threading.Thread(target=self._loop_decision, name="Decision", daemon=True).start()
        threading.Thread(target=self._loop_stdin, name="Stdin", daemon=True).start()

        log_event(self._log, "mock_startup",
                  detail={"classroom": self.cfg.classroom_id,
                          "server": self.cfg.server_url})
        self._print_status()
        # 主线程: 等待退出
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._log.info("KeyboardInterrupt")
            self._running = False
        return 0

    # ---------------------------------------------------------- 后台循环
    def _loop_time_sync(self) -> None:
        while self._running:
            ok = self._server.sync_time()
            if ok:
                drift = self._server.clock().drift_sec()
                self._log.info(
                    "%s time sync ok  offset=%+ds  drift=%ds",
                    colorize("●", C.GRN), self._server.clock().offset, drift,
                )
            else:
                self._log.warning(
                    "%s time sync FAIL (consecutive=%d)",
                    colorize("●", C.RED), self._server.clock().consecutive_failures,
                )
            time.sleep(self.cfg.time_sync_interval)

    def _loop_poll(self) -> None:
        while self._running:
            r = self._server.fetch_poll()
            if r.ok:
                self._time_slots = r.data.get("slots", [])
                ru = r.data.get("remote_unlock")
                if ru:
                    self._remote_until = int(ru.get("expires_at", 0))
                    self._remote_id = str(ru.get("command_id", ""))
                    self._log.info(
                        "%s remote unlock received  cmd=%s  expires_in=%ds",
                        colorize("●", C.YEL), self._remote_id,
                        max(0, self._remote_until - int(time.time())),
                    )
            else:
                self._log.info("%s poll fail: %s", colorize("●", C.RED), r.error)
            time.sleep(self.cfg.poll_interval)

    def _loop_log_upload(self) -> None:
        while self._running:
            n = self._server.upload_log_ring()
            if n:
                self._log.info("%s uploaded %d log items", colorize("●", C.BLU), n)
            time.sleep(self.cfg.log_upload_interval)

    def _loop_decision(self) -> None:
        """每秒根据上下文重新决策, 报告变化."""
        while self._running:
            try:
                now = int(time.time())
                server_now = self._server.clock().now() or now
                self._ctx.schedule = evaluate_schedule(
                    self._time_slots, now_epoch=server_now, soft_warn_sec=30,
                )
                self._ctx.has_valid_usb = self._usb_inserted
                self._ctx.remote.expires_at = self._remote_until
                self._ctx.remote.command_id = self._remote_id
                d, changed = self._state.update(self._ctx)
                if changed:
                    if d.state.value == "unlocked":
                        self._log.info(
                            "%s state: %s  source=%s  soft_warn=%s",
                            colorize("UNLOCK", C.GRN),
                            colorize("UNLOCKED", C.GRN),
                            colorize(d.reason.value, C.CYA),
                            d.soft_warn,
                        )
                    else:
                        self._log.info(
                            "%s state: %s  source=%s",
                            colorize("LOCK", C.RED),
                            colorize("LOCKED", C.RED),
                            colorize(d.reason.value, C.CYA),
                        )
            except Exception as e:
                self._log.exception("decision loop err: %s", e)
            time.sleep(1)

    def _loop_stdin(self) -> None:
        """主线程通过 stdin 命令交互."""
        print()
        print(colorize("命令: i=插入U盘 r=拔出 s=状态 p=poll t=time e=upload-logs q=退出", C.DIM))
        while self._running:
            try:
                line = input(colorize("> ", C.BOLD)).strip().lower()
            except EOFError:
                self._running = False
                return
            if not line:
                continue
            if line == "q":
                self._running = False
                return
            self._handle_command(line)

    def _handle_command(self, cmd: str) -> None:
        if cmd == "i":
            self._inject_usb_insert()
        elif cmd == "r":
            self._inject_usb_remove()
        elif cmd == "s":
            self._print_status()
        elif cmd == "p":
            r = self._server.fetch_poll()
            if r.ok:
                print(colorize("✓ poll ok", C.GRN))
                print(f"  slots={r.data.get('slots')}")
                print(f"  remote_unlock={r.data.get('remote_unlock')}")
            else:
                print(colorize(f"✗ poll fail: {r.error}", C.RED))
        elif cmd == "t":
            ok = self._server.sync_time()
            print(colorize("✓ time sync ok" if ok else "✗ time sync fail",
                           C.GRN if ok else C.RED))
        elif cmd == "e":
            n = self._server.upload_log_ring()
            print(colorize(f"uploaded {n} log items", C.GRN if n else C.DIM))
        elif cmd == "h" or cmd == "?":
            print("i/r/s/p/t/e/q")
        else:
            print(colorize(f"未知命令: {cmd!r}, 输入 h 查看帮助", C.YEL))

    # ---------------------------------------------------------- 事件注入
    def _inject_usb_insert(self) -> None:
        if not self.cfg.teacher_key or not Path(self.cfg.teacher_key).exists():
            print(colorize("✗ --teacher-key 路径无效", C.RED))
            return
        # 解析公钥
        pub_path = self.cfg.public_key or "data/public.pem"
        if not Path(pub_path).exists():
            print(colorize(f"✗ 公钥不存在: {pub_path} (用 --public 指定)", C.RED))
            return

        # 模拟挂载点: 用 /tmp/mock-usb
        mount = Path("/tmp/mock-usb")
        mount.mkdir(exist_ok=True)
        target = mount / "teacher.key"
        target.write_bytes(Path(self.cfg.teacher_key).read_bytes())

        from agent.usbmgr import UsbConfig, verify_teacher_key
        cfg = UsbConfig(teacher_key_filename="teacher.key")
        # 实际验签 (get_usb_serial 已被 monkey-patch)
        ev = verify_teacher_key(
            mount_root=mount, drive="M", cfg=cfg,
            public_key_pem=Path(pub_path).read_bytes(),
        )
        if ev.valid:
            self._usb_inserted = True
            self._event_count += 1
            log_event(self._log, EventType.USB_VERIFY_OK.value,
                      source=UnlockSource.USB.value,
                      detail={"drive": "M", "teacher": ev.teacher_name})
            print(colorize(f"✓ USB 验证通过  teacher={ev.teacher_name}", C.GRN))
        else:
            self._event_count += 1
            log_event(self._log, EventType.USB_VERIFY_FAIL.value,
                      source=UnlockSource.USB.value,
                      detail={"drive": "M", "reason": ev.reason})
            print(colorize(f"✗ USB 验证失败: {ev.reason}", C.RED))

    def _inject_usb_remove(self) -> None:
        self._usb_inserted = False
        log_event(self._log, EventType.USB_REMOVE.value,
                  detail={"drive": "M"})
        print(colorize("→ U 盘拔出 (5 秒后决策)", C.YEL))

    def _print_status(self) -> None:
        now = int(time.time())
        clock = self._server.clock()
        ru_left = max(0, self._remote_until - now)
        is_locked = self._state.state.value == "locked"
        locked_color = C.RED if is_locked else C.GRN
        locked_text = colorize(self._state.state.value.upper(), locked_color)
        in_session = self._ctx.schedule.in_session
        session_extra = f"  ({self._ctx.schedule.seconds_to_end}s left)" if in_session else ""
        ru_extra = f"  (cmd={self._remote_id})" if self._remote_id else ""

        print()
        print(colorize("─── 当前状态 ───", C.BOLD))
        print(f"  classroom    : {self.cfg.classroom_id}")
        print(f"  server       : {self.cfg.server_url}")
        print(f"  locked       : {locked_text}")
        print(f"  reason       : {self._state.reason.value}")
        print(f"  has_usb      : {self._usb_inserted}")
        print(f"  in_session   : {in_session}{session_extra}")
        print(f"  remote_until : {ru_left}s{ru_extra}")
        print(f"  time_offset  : {clock.offset:+d}s   drift={clock.drift_sec()}s")
        print(f"  online       : {self._server.is_online()}")
        print(f"  uptime       : {now - self._start_ts}s")
        print(f"  events_logged: {self._event_count}")
        print()

    def _banner(self) -> None:
        print(colorize("╔════════════════════════════════════════╗", C.CYA))
        print(colorize("║   Seewof Mock Agent (MacBook 联调)   ║", C.CYA))
        print(colorize("╚════════════════════════════════════════╝", C.CYA))
        print(f"  classroom : {self.cfg.classroom_id}")
        print(f"  server    : {self.cfg.server_url}")
        print(f"  serial    : {self.cfg.serial or '(none)'}")
        print(f"  teacher.key: {self.cfg.teacher_key or '(none)'}")
        print()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Seewof Mock Agent (跨平台)")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="启动 mock agent")
    run_p.add_argument("--server", required=True, help="seewof server URL")
    run_p.add_argument("--classroom", required=True)
    run_p.add_argument("--psk", required=True, help="教室预共享密钥 (>= 16 字符)")
    run_p.add_argument("--serial", help="模拟的 U 盘 serial (验签时用)")
    run_p.add_argument("--teacher-key", help="teacher.key 路径 (验签用)")
    run_p.add_argument("--public", help="public.pem 路径 (默认 data/public.pem)")
    run_p.add_argument("--time-sync-interval", type=float, default=10)
    run_p.add_argument("--poll-interval", type=float, default=5)
    run_p.add_argument("--log-upload-interval", type=float, default=8)

    args = p.parse_args()
    cfg = MockConfig(args)

    if len(cfg.psk) < 16:
        print("错误: --psk 必须 >= 16 字符", file=sys.stderr)
        return 2

    if args.cmd == "run":
        agent = MockAgent(cfg)
        return agent.run()
    return 1


if __name__ == "__main__":
    sys.exit(main())

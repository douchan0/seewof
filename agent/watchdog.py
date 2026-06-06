"""守护进程.

- 与主服务互相监视: 任一被 kill, 另一方立即重启它
- 写入心跳文件: <data_dir>/heartbeat
- 主服务定期更新心跳; watchdog 检查超时则拉起
- watchdog 自身用 TaskScheduler / 计划任务每分钟巡查一次, 防止自己被杀

设计权衡:
- 真正的"杀不掉"做不到, 只能"被杀后秒级恢复"
- 如果连 watchdog 都被杀, 计划任务会重启 watchdog
- 如果学生进安全模式, 见 protection.py + 组策略 (注册 Service 启动类型)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import AgentConfig, ConfigError


HEARTBEAT_FILE = "heartbeat"
HEARTBEAT_MAX_AGE = 15
POLL_INTERVAL = 3


def _hb_path(cfg: AgentConfig) -> Path:
    return Path(cfg.data_dir) / HEARTBEAT_FILE


def _is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        # Windows: OpenProcess, 这里用 os.kill 跨平台形式
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_hb(cfg: AgentConfig) -> dict | None:
    p = _hb_path(cfg)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_hb(cfg: AgentConfig, pid: int) -> None:
    p = _hb_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "pid": pid,
        "ts": int(time.time()),
    }), encoding="utf-8")


def _spawn_main(cfg: AgentConfig) -> int:
    """启动主服务, 返回新 PID."""
    py = sys.executable
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--service"]
    else:
        cmd = [py, "-m", "agent.main", "--config", _find_config_path()]
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
    proc = subprocess.Popen(
        cmd,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.pid


def _find_config_path() -> str:
    """简单: 与本脚本同目录的 agent.json."""
    return str(Path(__file__).resolve().parent / "agent.json")


def run_watchdog() -> int:
    p = argparse.ArgumentParser(description="Seewof Watchdog")
    p.add_argument("--config", required=True, help="path to agent.json")
    p.add_argument("--no-spawn", action="store_true",
                   help="do not auto-spawn main service (for tests)")
    args = p.parse_args()

    try:
        cfg = AgentConfig.load(args.config)
    except ConfigError as e:
        print(f"watchdog: config error: {e}", file=sys.stderr)
        return 2

    print(f"watchdog started; data_dir={cfg.data_dir}")
    last_spawn_attempt = 0.0

    while True:
        hb = _read_hb(cfg)
        need_spawn = False
        if hb is None:
            need_spawn = True
        else:
            age = time.time() - hb.get("ts", 0)
            pid = hb.get("pid", 0)
            if age > HEARTBEAT_MAX_AGE or not _is_alive(pid):
                need_spawn = True
                print(f"watchdog: main unhealthy (age={age:.1f}s, alive={_is_alive(pid)}); respawning")

        if need_spawn and not args.no_spawn and \
           time.time() - last_spawn_attempt > 5:
            try:
                pid = _spawn_main(cfg)
                _write_hb(cfg, pid)
                print(f"watchdog: spawned main pid={pid}")
            except Exception as e:
                print(f"watchdog: spawn failed: {e}", file=sys.stderr)
            last_spawn_attempt = time.time()

        time.sleep(POLL_INTERVAL)


def write_heartbeat(cfg: AgentConfig, pid: int) -> None:
    """主服务定期调用."""
    _write_hb(cfg, pid)


if __name__ == "__main__":
    sys.exit(run_watchdog())

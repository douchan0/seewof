"""配置加载.

配置文件为 JSON, 路径:
- 优先: 命令行 --config 指定
- 次之: 程序同目录 / ../../agent.json (开发)
- 再: %ProgramData%/SeewofAgent/config.json (安装)

任何非法字段立即抛出 ConfigError, 不允许"宽容解析"导致安全配置失效.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_ERRORS = (ValueError, KeyError, TypeError, json.JSONDecodeError)


class ConfigError(Exception):
    pass


@dataclass
class ServerConfig:
    base_url: str                       # https://192.168.1.10:8443
    psk: str                            # 预共享密钥
    verify_tls: bool = True             # 自签证书场景设为 False
    ca_cert: str | None = None          # CA 证书路径
    heartbeat_interval_sec: int = 60
    time_sync_interval_sec: int = 60
    request_timeout_sec: int = 8


@dataclass
class UsbConfig:
    serial_via: str = "wmi"             # wmi | volume_serial | instance_id
    teacher_key_filename: str = "teacher.key"
    public_key_path: str = ""           # 内置公钥 PEM 路径
    bind_drive_letters: list[str] = field(default_factory=list)  # 仅监听这些盘符


@dataclass
class LockConfig:
    block_keyboard: bool = True
    block_mouse: bool = True
    block_touch: bool = True
    block_accessibility: bool = True    # 屏蔽 Win+U, 讲述人, 粘滞键 等
    usb_remove_grace_sec: int = 5       # U盘拔出后延迟锁定秒数
    schedule_soft_warn_sec: int = 30    # 时段结束前 N 秒软提示
    overlay_opacity: float = 0.55
    overlay_message: str = "上课期间或插入教师 U 盘解锁"


@dataclass
class ProtectionConfig:
    watchdog_interval_sec: int = 3
    kill_self_grace_sec: int = 10
    admin_password_hash: str = ""       # bcrypt 哈希, 用于卸载/退出校验
    service_name: str = "SeewofAgent"


@dataclass
class AgentConfig:
    classroom_id: str
    server: ServerConfig
    usb: UsbConfig
    lock: LockConfig
    protection: ProtectionConfig
    log_dir: str = ""
    data_dir: str = ""

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path | None = None) -> "AgentConfig":
        path = _resolve_config_path(path)
        if not path.exists():
            raise ConfigError(f"config not found: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except CONFIG_ERRORS as e:
            raise ConfigError(f"failed to parse config {path}: {e}") from e

        return cls.from_dict(raw, base_dir=path.parent)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, base_dir: Path) -> "AgentConfig":
        try:
            classroom_id = str(raw["classroom_id"]).strip()
            if not classroom_id:
                raise ConfigError("classroom_id is empty")

            server_raw = raw["server"]
            server = ServerConfig(
                base_url=str(server_raw["base_url"]).rstrip("/"),
                psk=str(server_raw["psk"]),
                verify_tls=bool(server_raw.get("verify_tls", True)),
                ca_cert=_resolve_path(base_dir, server_raw.get("ca_cert")),
                heartbeat_interval_sec=int(server_raw.get("heartbeat_interval_sec", 60)),
                time_sync_interval_sec=int(server_raw.get("time_sync_interval_sec", 60)),
                request_timeout_sec=int(server_raw.get("request_timeout_sec", 8)),
            )
            if len(server.psk) < 16:
                raise ConfigError("server.psk must be >= 16 chars")

            usb_raw = raw.get("usb", {})
            usb = UsbConfig(
                serial_via=str(usb_raw.get("serial_via", "wmi")),
                teacher_key_filename=str(usb_raw.get("teacher_key_filename", "teacher.key")),
                public_key_path=_resolve_path(base_dir, usb_raw.get("public_key_path", "")),
                bind_drive_letters=[str(x).upper() for x in usb_raw.get("bind_drive_letters", [])],
            )

            lock_raw = raw.get("lock", {})
            lock = LockConfig(
                block_keyboard=bool(lock_raw.get("block_keyboard", True)),
                block_mouse=bool(lock_raw.get("block_mouse", True)),
                block_touch=bool(lock_raw.get("block_touch", True)),
                block_accessibility=bool(lock_raw.get("block_accessibility", True)),
                usb_remove_grace_sec=int(lock_raw.get("usb_remove_grace_sec", 5)),
                schedule_soft_warn_sec=int(lock_raw.get("schedule_soft_warn_sec", 30)),
                overlay_opacity=float(lock_raw.get("overlay_opacity", 0.55)),
                overlay_message=str(lock_raw.get("overlay_message", "上课期间或插入教师 U 盘解锁")),
            )

            prot_raw = raw.get("protection", {})
            protection = ProtectionConfig(
                watchdog_interval_sec=int(prot_raw.get("watchdog_interval_sec", 3)),
                kill_self_grace_sec=int(prot_raw.get("kill_self_grace_sec", 10)),
                admin_password_hash=str(prot_raw.get("admin_password_hash", "")),
                service_name=str(prot_raw.get("service_name", "SeewofAgent")),
            )

            log_dir = _resolve_path(base_dir, raw.get("log_dir", "logs"))
            data_dir = _resolve_path(base_dir, raw.get("data_dir", "data"))

            cfg = cls(
                classroom_id=classroom_id,
                server=server,
                usb=usb,
                lock=lock,
                protection=protection,
                log_dir=str(log_dir),
                data_dir=str(data_dir),
            )
            cfg._ensure_dirs()
            return cfg
        except (KeyError, TypeError, ValueError) as e:
            raise ConfigError(f"invalid config: {e}") from e

    def _ensure_dirs(self) -> None:
        for d in (self.log_dir, self.data_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _resolve_path(base: Path, value: str | None) -> str:
    """解析路径, 相对路径相对 base."""
    if not value:
        return ""
    p = Path(value)
    if not p.is_absolute():
        p = base / p
    return str(p)


def _resolve_config_path(explicit: str | Path | None) -> Path:
    if explicit:
        return Path(explicit)
    candidates: list[Path] = []
    # 1. 程序所在目录
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "agent.json")
    candidates.append(Path(__file__).resolve().parent / "agent.json")
    # 2. %ProgramData%
    if os.name == "nt":
        pd = os.environ.get("ProgramData", "C:/ProgramData")
        candidates.append(Path(pd) / "SeewofAgent" / "config.json")
    # 3. 当前目录
    candidates.append(Path.cwd() / "agent.json")

    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # 都不存在也返回第一个, 让上层抛 FileNotFoundError

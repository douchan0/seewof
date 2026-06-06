"""U 盘监听与签名验证.

依赖:
- Windows: psutil (检测 mount) + wmi (获取 PNPDeviceID 序列号) + pywin32 (WM_DEVICECHANGE)
- 任意: cryptography (RSA 验签)

设计:
- UsbMonitor: 后台线程轮询挂载点, 检测插拔
- 验证: 读 teacher.key -> RSA 验签 -> 校验 serial 一致 -> 校验有效期
- 任何一步失败: 视为非法 U 盘, 不解锁, 上报日志
"""

from __future__ import annotations

import os
import string
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.crypto import (
    UsbKeyPayload,
    load_public_key,
    rsa_verify,
    unpack_teacher_key,
)
from common.protocol import EventType, UnlockSource

from .config import UsbConfig
from .logger import log_event


@dataclass
class UsbEvent:
    drive: str
    serial: str
    valid: bool
    teacher_id: str = ""
    teacher_name: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# 平台相关: 获取 USB 设备序列号
# ---------------------------------------------------------------------------
def _get_volume_serial_windows(drive: str) -> str:
    """通过 Win32 API 获取卷序列号 (VolumeSerialNumber), 每次格式化会变.

    仅为 fallback, 主策略使用 WMI PNPDeviceID.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(256)
        name = f"{drive}\\\\"
        rc = kernel32.GetVolumeInformationW(
            name, None, 0, None, None, None, buf, 256,
        )
        if rc == 0:
            return ""
        # 卷序列号需要单独获取
        serial = ctypes.c_ulong(0)
        kernel32.GetVolumeInformationW(
            name, None, 0,
            ctypes.byref(serial), None, None, None, 0,
        )
        return f"VOL:{serial.value:08X}"
    except Exception:
        return ""


def _get_serial_via_wmi(drive: str) -> str:
    """通过 WMI 获取 USB 磁盘的 PNPDeviceID (硬件级, 格式化不变).

    优先返回 PNPDeviceID 的去前缀形式: 'USBSTOR\\DISK&VEN_...&PROD_...\\...&0'.
    """
    try:
        import wmi  # type: ignore
    except ImportError:
        return ""

    try:
        c = wmi.WMI()
        # 根据盘符找到逻辑磁盘
        logical = c.Win32_LogicalDisk(DeviceID=drive.upper() + ":")
        if not logical:
            return ""
        # 找分区
        for ld in logical:
            for part in c.Win32_DiskPartition(DeviceID=ld.DeviceID):
                # 找磁盘驱动器
                for disk in c.Win32_DiskDrive(Index=part.DiskIndex):
                    if "USB" in (disk.PNPDeviceID or "").upper() or \
                       "REMOVABLE" in (disk.MediaType or "").upper():
                        return disk.PNPDeviceID or ""
        return ""
    except Exception:
        return ""


def get_usb_serial(drive: str, method: str = "wmi") -> str:
    drive = drive.rstrip(":\\").rstrip(":").upper() + ":"
    if method == "wmi":
        s = _get_serial_via_wmi(drive)
        if s:
            return s
    return _get_volume_serial_windows(drive)


# ---------------------------------------------------------------------------
# teacher.key 读取 + 验签
# ---------------------------------------------------------------------------
def _read_teacher_key(mount_root: Path, filename: str) -> bytes | None:
    """读取 U 盘上的 teacher.key, 容忍 1KB 内的 BOM/空白."""
    p = mount_root / filename
    try:
        if not p.exists():
            return None
        data = p.read_bytes()
        if len(data) > 4096:
            return None
        return data.strip()
    except (PermissionError, OSError):
        return None


def verify_teacher_key(
    *,
    mount_root: Path,
    drive: str,
    cfg: UsbConfig,
    public_key_pem: bytes,
) -> UsbEvent:
    """对单个挂载点执行完整验签流程.

    返回 UsbEvent.valid=False 时, reason 描述失败原因 (供日志).
    """
    serial = get_usb_serial(drive, cfg.serial_via)
    if not serial:
        return UsbEvent(
            drive=drive, serial="", valid=False,
            reason="cannot read usb serial",
        )

    raw = _read_teacher_key(mount_root, cfg.teacher_key_filename)
    if raw is None:
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            reason=f"teacher.key not found: {cfg.teacher_key_filename}",
        )

    try:
        payload, sig = unpack_teacher_key(raw)
    except ValueError as e:
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            reason=f"teacher.key parse: {e}",
        )

    # 序列号一致性
    if payload.serial.upper() != serial.upper():
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            reason=f"serial mismatch key={payload.serial} actual={serial}",
        )

    # 有效期
    if payload.is_expired():
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            teacher_id=payload.teacher_id,
            teacher_name=payload.teacher_name,
            reason="teacher.key expired",
        )

    # RSA 验签
    try:
        pub = load_public_key(public_key_pem)
    except Exception as e:
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            reason=f"load public key failed: {e}",
        )

    if not rsa_verify(pub, sig, payload.canonical_json()):
        return UsbEvent(
            drive=drive, serial=serial, valid=False,
            teacher_id=payload.teacher_id,
            teacher_name=payload.teacher_name,
            reason="rsa signature invalid",
        )

    return UsbEvent(
        drive=drive, serial=serial, valid=True,
        teacher_id=payload.teacher_id, teacher_name=payload.teacher_name,
    )


# ---------------------------------------------------------------------------
# 监听器: 线程 + 轮询
# ---------------------------------------------------------------------------
class UsbMonitor:
    """定期轮询盘符, 触发 on_change 回调.

    on_change(UsbEvent) 在 USB 插入/拔出/验证状态变化时调用.
    拔出不验证 (因为已移除), 但产生 USB_REMOVE 事件.
    """

    def __init__(
        self,
        cfg: UsbConfig,
        public_key_pem: bytes,
        on_insert: Callable[[UsbEvent], None],
        on_remove: Callable[[str], None],
        *,
        poll_interval: float = 1.5,
    ) -> None:
        self._cfg = cfg
        self._pub = public_key_pem
        self._on_insert = on_insert
        self._on_remove = on_remove
        self._poll = poll_interval
        self._known: set[str] = set()           # 已挂载的盘符
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="UsbMonitor", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        import psutil
        while self._running:
            try:
                current = self._scan(psutil)
            except Exception as e:  # noqa: BLE001
                # 监控器自己挂了不影响主服务
                log_event(__import__("logging").getLogger("seewof"),
                          "usb_scan_error", detail={"err": str(e)},
                          level=__import__("logging").WARNING)
                time.sleep(self._poll)
                continue

            # 新插入
            for d in current - self._known:
                self._handle_insert(d)
            # 拔出
            for d in self._known - current:
                self._on_remove(d)
                log_event(__import__("logging").getLogger("seewof"),
                          EventType.USB_REMOVE.value, detail={"drive": d})

            self._known = current
            time.sleep(self._poll)

    def _scan(self, psutil_mod) -> set[str]:
        result: set[str] = set()
        for part in psutil_mod.disk_partitions(all=False):
            if not part.device:
                continue
            d = part.device[0].upper()
            if self._cfg.bind_drive_letters and d not in self._cfg.bind_drive_letters:
                continue
            if os.name == "nt" and "removable" in part.opts.lower() or \
               "fixed" not in part.opts.lower():
                result.add(d)
        return result

    def _handle_insert(self, drive: str) -> None:
        mount_root = Path(f"{drive}:/")
        ev = verify_teacher_key(
            mount_root=mount_root, drive=drive,
            cfg=self._cfg, public_key_pem=self._pub,
        )
        if ev.valid:
            log_event(__import__("logging").getLogger("seewof"),
                      EventType.USB_VERIFY_OK.value,
                      source=UnlockSource.USB.value,
                      detail={"drive": drive, "teacher": ev.teacher_name})
        else:
            log_event(__import__("logging").getLogger("seewof"),
                      EventType.USB_VERIFY_FAIL.value,
                      source=UnlockSource.USB.value,
                      detail={"drive": drive, "reason": ev.reason},
                      level=__import__("logging").WARNING)
        self._on_insert(ev)

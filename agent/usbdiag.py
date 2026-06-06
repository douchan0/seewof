"""教室端 USB 诊断工具.

用法 (Windows 上以管理员运行):
    python -m agent.usbdiag              # 列出所有可移动磁盘
    python -m agent.usbdiag --drive E   # 读取 E 盘的硬件序列号 + teacher.key 信息

供教师在签发 U 盘前, 先在 Windows 上获取该 U 盘的 PNPDeviceID.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .usbmgr import get_usb_serial, verify_teacher_key
from .config import UsbConfig


def list_removable() -> list[dict[str, str]]:
    import psutil
    out = []
    for part in psutil.disk_partitions(all=False):
        d = (part.device or "")[0:1].upper()
        if not d:
            continue
        if "removable" in (part.opts or "").lower() or \
           "fixed" not in (part.opts or "").lower():
            out.append({
                "drive": d,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
            })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Seewof USB 诊断")
    p.add_argument("--drive", help="盘符, e.g. E")
    p.add_argument("--public", help="public.pem 路径, 用于验证 teacher.key")
    p.add_argument("--teacher-key", default="teacher.key",
                   help="teacher.key 文件名 (默认 teacher.key)")
    p.add_argument("--serial-via", default="wmi")
    args = p.parse_args()

    if not args.drive:
        # 列出所有
        for r in list_removable():
            print(f"{r['drive']}:  {r['mountpoint']}  ({r['fstype']})")
        return 0

    d = args.drive.rstrip(":\\").rstrip(":").upper()
    serial = get_usb_serial(d, args.serial_via)
    print(f"drive       : {d}:")
    print(f"serial      : {serial}")

    if args.public and Path(args.public).exists():
        cfg = UsbConfig(teacher_key_filename=args.teacher_key)
        ev = verify_teacher_key(
            mount_root=Path(f"{d}:/"),
            drive=d, cfg=cfg,
            public_key_pem=Path(args.public).read_bytes(),
        )
        print(f"teacher.key : {'VALID' if ev.valid else 'INVALID'}")
        if ev.valid:
            print(f"  teacher_id   : {ev.teacher_id}")
            print(f"  teacher_name : {ev.teacher_name}")
        else:
            print(f"  reason       : {ev.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""自我保护: 辅助功能屏蔽, 任务管理器禁用, 卸载保护.

注意: 本模块的多数操作需要 SYSTEM / 管理员权限, 因此通常由 Windows 服务调用.

策略:
- 禁用任务管理器: HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System
  -> DisableTaskMgr = 1
- 禁用轻松使用: 同上 -> NoEaseOfAccess
- 禁用锁屏设置: NoLockScreen
- 隐藏控制面板: NoControlPanel
- 退出保护: 进程收到自定义信号时, 校验管理员密码 (bcrypt)
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import winreg  # type: ignore
from dataclasses import dataclass

if sys.platform != "win32":
    raise ImportError("protection is Windows-only")


_POLICY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
_EXPLORER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"


@dataclass
class PolicySnapshot:
    """在锁定前后保存/恢复策略, 解锁时不留痕迹."""

    saved: dict[tuple[str, str], int | None] = None

    def __post_init__(self) -> None:
        self.saved = {}


def _reg_write(hive: int, subkey: str, name: str, value: int) -> None:
    try:
        with winreg.CreateKeyEx(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
    except PermissionError:
        pass


def _reg_delete(hive: int, subkey: str, name: str) -> None:
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


class Protection:
    """应用/撤销 Windows 策略. 仅在锁定时启用, 解锁时还原."""

    def __init__(self) -> None:
        self._log = logging.getLogger("seewof")
        self._saved: dict[tuple[int, str, str], int | None] = {}
        self._applied = False

    def apply(self) -> None:
        if self._applied:
            return
        if not _is_admin():
            self._log.warning("not admin; some policies may not apply")
        # 1. 任务管理器
        self._save_and_set(winreg.HKEY_CURRENT_USER, _POLICY_KEY, "DisableTaskMgr", 1)
        # 2. 轻松使用
        self._save_and_set(winreg.HKEY_CURRENT_USER, _POLICY_KEY, "NoEaseOfAccess", 1)
        # 3. 锁屏
        self._save_and_set(winreg.HKEY_CURRENT_USER, _POLICY_KEY, "NoLockScreen", 1)
        # 4. 控制面板
        self._save_and_set(winreg.HKEY_CURRENT_USER, _POLICY_KEY, "NoControlPanel", 1)
        # 5. 运行对话框
        self._save_and_set(winreg.HKEY_CURRENT_USER, _POLICY_KEY, "NoRun", 1)
        # 6. 文件资源管理器上下文菜单
        self._save_and_set(winreg.HKEY_CURRENT_USER, _EXPLORER_KEY, "NoViewContextMenu", 1)
        self._applied = True
        self._log.info("protection policies applied")

    def revert(self) -> None:
        if not self._applied:
            return
        for (hive, subkey, name), orig in self._saved.items():
            if orig is None:
                _reg_delete(hive, subkey, name)
            else:
                _reg_write(hive, subkey, name, orig)
        self._saved.clear()
        self._applied = False
        self._log.info("protection policies reverted")

    def _save_and_set(self, hive: int, subkey: str, name: str, value: int) -> None:
        orig = _reg_read_dword(hive, subkey, name)
        self._saved[(hive, subkey, name)] = orig
        _reg_write(hive, subkey, name, value)


def _reg_read_dword(hive: int, subkey: str, name: str) -> int | None:
    try:
        with winreg.OpenKey(hive, subkey) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return int(v)
    except FileNotFoundError:
        return None
    except (PermissionError, OSError):
        return None


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 退出保护: 校验管理员密码
# ---------------------------------------------------------------------------
def verify_admin_password(password: str, bcrypt_hash: str) -> bool:
    """bcrypt 校验. 不存在 bcrypt 时, 仅在开发环境允许.

    生产: pip install bcrypt
    """
    if not bcrypt_hash:
        return False
    try:
        import bcrypt  # type: ignore
        return bcrypt.checkpw(password.encode("utf-8"), bcrypt_hash.encode("utf-8"))
    except ImportError:
        # 没有 bcrypt 库时直接拒绝, 防"明文回退"
        return False

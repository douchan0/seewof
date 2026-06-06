"""输入拦截.

策略:
1. 键盘 + 鼠标: 使用 Win32 低级钩子 (WH_KEYBOARD_LL / WH_MOUSE_LL) 拦截
2. 触摸: 通过 SetupAPI 临时禁用 HID-compliant touch screen 设备 (Enable/Disable)
3. 辅助功能: 屏蔽 Win+U, Win+Ctrl+S (搜索), Ctrl+Alt+Del 不可屏蔽 (Win 自身处理)
   -> 我们用 BlockInput + 自定义 LL 钩子吞掉大多数键
4. 任务管理器: 通过组策略/注册表禁用, 见 protection.py

为何不直接 BlockInput:
- BlockInput 会同时阻止我们自己 (Agent 进程) 的输入, 不好维护
- 钩子可精细控制: 解锁时直接放行
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable

from .config import LockConfig

if sys.platform != "win32":
    raise ImportError("input_blocker is Windows-only")


# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207

# 用于识别 Win 组合键的虚拟键码
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_F4 = 0x73
VK_U = 0x55
VK_S = 0x53
VK_DELETE = 0x2E

# Mod 标识
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 鼠标钩子结构
class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.c_wchar_p, ctypes.c_void_p,
)

# 屏蔽的鼠标消息集合
_MOUSE_MSGS_TO_BLOCK = frozenset({
    WM_LBUTTONDOWN, WM_LBUTTONUP,
    WM_RBUTTONDOWN, WM_MBUTTONDOWN,
    WM_MOUSEMOVE, WM_MOUSEWHEEL,
})

# 允许的 Win 组合键 (留给 OS/Agent 自己用, 永远不屏蔽)
_ALWAYS_ALLOW = frozenset()


# ---------------------------------------------------------------------------
# InputBlocker
# ---------------------------------------------------------------------------
class InputBlocker:
    """控制键盘/鼠标低级钩子.

    状态:
    - locked=True: 拦截键盘鼠标
    - locked=False: 全部放行

    线程模型: 钩子回调运行在系统注入的线程, 必须快且无副作用.
    """

    def __init__(
        self,
        cfg: LockConfig,
        *,
        on_hotkey: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._locked = True
        self._lock = threading.RLock()
        self._log = logging.getLogger("seewof")
        self._on_hotkey = on_hotkey
        self._kb_hook = None
        self._ms_hook = None
        self._kb_proc = None
        self._ms_proc = None
        self._running = False

    # ------------------------------------------------------------------ pub
    def start(self) -> None:
        if self._running:
            return
        user32 = ctypes.windll.user32
        # 注册键盘钩子
        self._kb_proc = HOOKPROC(self._kb_callback)
        self._kb_hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kb_proc,
            ctypes.windll.kernel32.GetModuleHandleW(None), 0,
        )
        if not self._kb_hook:
            raise OSError("SetWindowsHookExW(KEYBOARD_LL) failed")
        # 注册鼠标钩子
        self._ms_proc = HOOKPROC(self._ms_callback)
        self._ms_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._ms_proc,
            ctypes.windll.kernel32.GetModuleHandleW(None), 0,
        )
        if not self._ms_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
            raise OSError("SetWindowsHookExW(MOUSE_LL) failed")
        self._running = True
        self._log.info("input_blocker started: kb_hook=%s ms_hook=%s",
                       self._kb_hook, self._ms_hook)

    def stop(self) -> None:
        user32 = ctypes.windll.user32
        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._ms_hook:
            user32.UnhookWindowsHookEx(self._ms_hook)
            self._ms_hook = None
        self._running = False
        self._log.info("input_blocker stopped")

    def set_locked(self, locked: bool) -> None:
        with self._lock:
            self._locked = locked
            self._log.info("input_blocker state -> %s", "LOCKED" if locked else "UNLOCKED")

    @property
    def is_locked(self) -> bool:
        return self._locked

    # --------------------------------------------------------------- hooks
    def _kb_callback(self, nCode, wParam, lParam):
        if nCode != HC_ACTION or not self._locked:
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        if not self._cfg.block_keyboard:
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = info.vkCode
        msg = wParam

        # 解锁快捷键: Ctrl+Alt+Shift+F12 永远不屏蔽 (供 Agent 本地调试 + 隐藏托盘)
        # 此处通过 on_hotkey 通知, 但仍放行
        if vk == 0x7B and (ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000) \
                and (ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000) \
                and (ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000):
            if self._on_hotkey:
                self._on_hotkey("debug_panel")
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        # 屏蔽 Win+U (轻松使用) / Win+S (搜索) / Alt+Tab / Alt+F4 / Ctrl+Esc
        # 已被 BlockInput 替代/补充策略
        if self._cfg.block_accessibility:
            if self._is_disallowed_combo(vk):
                return 1  # 吞掉

        return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _ms_callback(self, nCode, wParam, lParam):
        if nCode != HC_ACTION or not self._locked:
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        if not self._cfg.block_mouse:
            return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

        msg = wParam
        if msg in _MOUSE_MSGS_TO_BLOCK:
            return 1  # 吞掉

        return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _is_disallowed_combo(self, vk: int) -> bool:
        """检测 Win+U, Win+S, Alt+Tab, Alt+F4, Ctrl+Esc 等可绕过辅助功能的组合."""
        win_down = (ctypes.windll.user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or \
                   (ctypes.windll.user32.GetAsyncKeyState(VK_RWIN) & 0x8000)
        alt_down = ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000      # VK_MENU
        ctrl_down = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000     # VK_CONTROL
        esc_down = vk == VK_ESCAPE

        if win_down and vk in (VK_U, VK_S):
            return True
        if alt_down and vk in (VK_TAB, VK_F4):
            return True
        if ctrl_down and esc_down:
            return True
        return False


# ---------------------------------------------------------------------------
# 触摸拦截: SetupAPI 禁用/启用 HID Touch 设备
# ---------------------------------------------------------------------------
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
SPDRP_HARDWAREID = 0x00000001
SPDRP_DEVICEDESC = 0x00000000
DICS_DISABLE = 0x00000002
DICS_ENABLE = 0x00000001
DICS_FLAG_GLOBAL = 0x00000001

_setupapi = ctypes.windll.setupapi
cfgmgr = ctypes.windll.cfgmgr32


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", ctypes.c_byte * 16),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_CLASSINSTALL_HEADER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InstallFunction", wintypes.DWORD),
    ]


class SP_PROPCHANGE_PARAMS(ctypes.Structure):
    _fields_ = [
        ("ClassInstallHeader", SP_CLASSINSTALL_HEADER),
        ("StateChange", wintypes.DWORD),
        ("Scope", wintypes.DWORD),
        ("HwProfile", wintypes.DWORD),
    ]


def _disable_touch_devices(disable: bool) -> int:
    """禁用/启用所有 HID-compliant touch screen 设备.

    返回受影响的设备数. 需要以管理员/服务权限运行.
    """
    DIGCF_DIGITALDRIVER = 0x00000080
    flags = DIGCF_PRESENT | DIGCF_DIGITALDRIVER

    # HIDClass GUID
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_byte * 8)]
    hid_guid = GUID(
        0x4d1e55b2, 0xf16f, 0x11cf,
        (ctypes.c_byte * 8)(0x88, 0xcb, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
    )
    info = ctypes.create_unicode_buffer(2048)

    hdevinfo = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(hid_guid), None, None, flags,
    )
    if hdevinfo == -1:
        return 0

    affected = 0
    idx = 0
    while True:
        did = SP_DEVINFO_DATA()
        did.cbSize = ctypes.sizeof(did)
        if not _setupapi.SetupDiEnumDeviceInfo(hdevinfo, idx, ctypes.byref(did)):
            break
        # 读 hardware id
        _setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdevinfo, ctypes.byref(did), 0, None,
            ctypes.cast(info, ctypes.c_wchar_p), 2048, None,
        )
        hw = info.value.upper()
        if "HID" in hw and "TOUCH" in hw or \
           "VID_" in hw and ("DIGITIZER" in hw or "TOUCH" in hw):
            # 构造属性变更
            params = SP_PROPCHANGE_PARAMS()
            params.ClassInstallHeader.cbSize = ctypes.sizeof(SP_CLASSINSTALL_HEADER)
            params.ClassInstallHeader.InstallFunction = 0x0018  # DIF_PROPERTYCHANGE
            params.StateChange = DICS_DISABLE if disable else DICS_ENABLE
            params.Scope = DICS_FLAG_GLOBAL
            _setupapi.SetupDiSetClassInstallParamsW(
                hdevinfo, ctypes.byref(did),
                ctypes.cast(ctypes.byref(params), ctypes.c_void_p),
                ctypes.sizeof(params),
            )
            ok = _setupapi.SetupDiCallClassInstaller(
                0x0018, hdevinfo, ctypes.byref(did),
            )
            if ok:
                affected += 1
        idx += 1

    _setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)
    return affected


class TouchBlocker:
    """通过 SetupAPI 临时禁用/启用触摸设备."""

    def __init__(self) -> None:
        self._disabled = False
        self._log = logging.getLogger("seewof")

    def set_blocked(self, blocked: bool) -> None:
        if blocked and not self._disabled:
            n = _disable_touch_devices(True)
            self._disabled = True
            self._log.info("touch disabled (%d devices)", n)
        elif not blocked and self._disabled:
            n = _disable_touch_devices(False)
            self._disabled = False
            self._log.info("touch enabled (%d devices)", n)

    def force_enable(self) -> None:
        """在退出前强制恢复, 避免用户被永久锁住."""
        if self._disabled:
            self._disable_touch_devices(False)
            self._disabled = False

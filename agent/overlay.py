"""锁定状态遮罩 UI.

设计目标:
- 半透明覆盖在桌面, 不夺取焦点, 不阻塞后台应用
- 锁定时显示; 解锁时立即隐藏
- 软提示 (软锁定) 切换为更轻量的形式 (顶部黄条)

实现: PyQt5 透明无边框顶层窗口, WS_EX_TRANSPARENT + WS_EX_LAYERED + WS_EX_TOOLWINDOW
- WS_EX_TRANSPARENT: 鼠标穿透, 不会拦截我们的钩子也不会让学生点中
- WS_EX_TOOLWINDOW: 不在任务栏/Alt-Tab 中出现
- 使用 QTimer 监控状态变化
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable

from .config import LockConfig

if sys.platform != "win32":
    raise ImportError("overlay is Windows-only")


def _set_window_passthrough(hwnd: int) -> None:
    """给窗口加 WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW 风格, 实现鼠标穿透."""
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000

    user32 = ctypes.windll.user32
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


class Overlay:
    """控制台遮罩. 使用 PyQt5 渲染半透明窗口.

    使用方法:
        ov = Overlay(cfg)
        ov.start()           # 启动 Qt 事件循环 (阻塞)
        ov.show_lock()       # 在另一线程调用, 切到锁定
        ov.show_unlock()     # 切到解锁 (隐藏)
    """

    def __init__(self, cfg: LockConfig) -> None:
        self._cfg = cfg
        self._log = logging.getLogger("seewof")
        self._app = None
        self._win = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._mode: str = "hidden"  # hidden | locked | soft_warn

    def start(self) -> None:
        """在子线程启动 Qt 事件循环."""
        if self._thread:
            return
        self._thread = threading.Thread(target=self._qt_loop, name="Overlay", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        if self._app is not None:
            try:
                self._app.quit()
            except Exception:
                pass

    # ------------------------------------------------------------------ API
    def show_lock(self) -> None:
        self._post(lambda: self._set_mode("locked"))

    def show_unlock(self) -> None:
        self._post(lambda: self._set_mode("hidden"))

    def show_soft_warn(self, message: str | None = None) -> None:
        msg = message or "即将锁定, 请插入教师 U 盘或结束当前操作"
        self._post(lambda: self._set_mode("soft_warn", message=msg))

    # --------------------------------------------------------------- Qt
    def _post(self, fn: Callable[[], None]) -> None:
        if not self._app or not self._win:
            return
        try:
            from PyQt5.QtCore import QMetaObject, Qt, Q_ARG  # type: ignore
            QMetaObject.invokeMethod(
                self._win, "_apply_mode",
                Qt.QueuedConnection,
                Q_ARG("QVariantList", [fn.__name__]),
            )
        except Exception:
            # fallback: 直接调用 (单线程)
            try:
                fn()
            except Exception as e:
                self._log.warning("overlay post failed: %s", e)

    def _qt_loop(self) -> None:
        try:
            from PyQt5.QtCore import Qt, QTimer
            from PyQt5.QtGui import QColor, QFont, QPainter, QBrush
            from PyQt5.QtWidgets import QApplication, QWidget
        except ImportError:
            self._log.error("PyQt5 not installed; overlay disabled")
            return

        app = QApplication.instance() or QApplication(sys.argv)
        self._app = app

        class _Win(QWidget):
            def __init__(self, overlay: "Overlay") -> None:
                super().__init__()
                self._overlay = overlay
                self._message = overlay._cfg.overlay_message
                self._mode = "hidden"
                # 透明无边框顶层窗口
                self.setWindowFlags(
                    Qt.FramelessWindowHint
                    | Qt.WindowStaysOnTopHint
                    | Qt.Tool
                )
                self.setAttribute(Qt.WA_TranslucentBackground, True)
                self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                self.resize(1920, 1080)
                # 跨多屏
                for s in app.screens():
                    pass  # Qt 自动

            def paintEvent(self, _):
                if self._mode == "hidden":
                    return
                p = QPainter(self)
                if self._mode == "locked":
                    p.setBrush(QBrush(QColor(0, 0, 0, int(255 * self._overlay._cfg.overlay_opacity))))
                    p.drawRect(self.rect())
                    p.setPen(QColor(255, 255, 255))
                    f = QFont("Microsoft YaHei", 28, QFont.Bold)
                    p.setFont(f)
                    p.drawText(self.rect(), Qt.AlignCenter, self._message)
                elif self._mode == "soft_warn":
                    p.setBrush(QBrush(QColor(255, 193, 7, 200)))
                    h = 80
                    p.drawRect(0, 0, self.width(), h)
                    p.setPen(QColor(0, 0, 0))
                    f = QFont("Microsoft YaHei", 16, QFont.Bold)
                    p.setFont(f)
                    p.drawText(self.rect().adjusted(20, 20, -20, -20),
                               Qt.AlignVCenter | Qt.AlignLeft, self._message)
                p.end()

            def _apply_mode(self, *_):
                # 由 invokeMethod 调用, args 忽略
                self._mode = self._overlay._mode
                self._message = self._overlay._message
                self.update()

        win = _Win(self)
        self._win = win
        win.show()
        # 让鼠标穿透
        QTimer.singleShot(50, lambda: _set_window_passthrough(int(win.winId())))

        self._ready.set()
        app.exec_()

    def _set_mode(self, mode: str, message: str | None = None) -> None:
        self._mode = mode
        if message:
            self._message = message
        else:
            self._message = self._cfg.overlay_message
        if self._win is not None:
            self._win._mode = mode
            self._win._message = self._message
            self._win.update()
            if mode == "hidden":
                self._win.hide()
            else:
                self._win.show()
                self._win.raise_()

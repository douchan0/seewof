"""Windows 服务封装.

将 Agent 注册为 Windows 服务, 以 SYSTEM 权限运行, 开机自启, 支持重启.

依赖: pywin32 (pywin32 必须用 'pip install pywin32' 完整安装)

部署:
    python -m agent.service install       # 注册服务
    python -m agent.service start         # 启动
    python -m agent.service stop          # 停止
    python -m agent.service remove        # 卸载
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

from .config import AgentConfig

if os.name != "nt":
    raise ImportError("Windows service only")


def run_as_service(cfg: AgentConfig) -> None:
    import win32serviceutil  # type: ignore
    import win32service      # type: ignore
    import win32event         # type: ignore

    class SeewofAgentService(win32serviceutil.ServiceFramework):
        _svc_name_ = cfg.protection.service_name
        _svc_display_name_ = "Seewof Classroom Lock Agent"
        _svc_description_ = "Locks classroom computer input except during class hours or with teacher USB."
        _svc_deps_ = ["RpcSs"]

        def __init__(self, args):
            super().__init__(args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._agent = None
            self._hb_thread: threading.Thread | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)
            if self._agent:
                threading.Thread(target=self._agent.stop, daemon=True).start()

        def SvcDoRun(self) -> None:
            from .main import Agent
            from .watchdog import write_heartbeat
            import servicemanager  # type: ignore

            try:
                self._agent = Agent(cfg)
                self.ReportServiceStatus(win32service.SERVICE_RUNNING)
                # 心跳写线程
                self._hb_thread = threading.Thread(
                    target=self._heartbeat, args=(cfg, write_heartbeat),
                    daemon=True,
                )
                self._hb_thread.start()
                # 跑 Agent
                self._agent.run()
            except Exception as e:
                servicemanager.LogErrorMsg(f"SeewofAgent fatal: {e}")
                self.SvcStop()

        def _heartbeat(self, cfg: AgentConfig, write_hb) -> None:
            while True:
                try:
                    write_hb(cfg, os.getpid())
                except Exception:
                    pass
                # 5 秒一次
                if hasattr(win32event, "WaitForSingleObject"):
                    win32event.WaitForSingleObject(self._stop_event, 5000)
                else:
                    time.sleep(5)
                if not self._agent._running:
                    break

    win32serviceutil.HandleCommandLine(SeewofAgentService)

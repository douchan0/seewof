"""与管理端 HTTP 通信.

- 所有请求带 HMAC 签名
- 心跳 + 日志上传
- 拉取时间 / 时段 / 远程指令
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
import urllib3

from common.crypto import (
    SIGNATURE_HEADER, TIMESTAMP_HEADER, NONCE_HEADER,
    build_signed_request, verify_signed_request,
)
from common.time_sync import SmoothedClock, TimeSample

from .config import ServerConfig
from .logger import drain_ring, log_event


@dataclass
class ServerReply:
    ok: bool
    data: dict[str, Any]
    error: str = ""


class ServerClient:
    """封装教室端 -> 管理端的所有 HTTP 调用."""

    def __init__(self, cfg: ServerConfig, classroom_id: str) -> None:
        self._cfg = cfg
        self._cid = classroom_id
        self._log = logging.getLogger("seewof")
        self._clock = SmoothedClock()
        self._session = self._build_session()
        self._online = False

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        if not self._cfg.verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        s.verify = self._cfg.verify_tls
        if self._cfg.ca_cert:
            s.verify = self._cfg.ca_cert
        return s

    # ------------------------------------------------------------------ pub
    def is_online(self) -> bool:
        return self._online

    def clock(self) -> SmoothedClock:
        return self._clock

    def sync_time(self) -> bool:
        """单次时间同步, 失败返回 False."""
        url = f"{self._cfg.base_url}/api/v1/agent/time"
        before = int(time.time())
        body = b""
        headers = build_signed_request(self._cfg.psk.encode("utf-8"), body)
        try:
            r = self._session.get(
                url, headers=headers,
                timeout=self._cfg.request_timeout_sec,
            )
            after = int(time.time())
        except requests.RequestException as e:
            self._clock.record_failure()
            self._set_online(False, reason=str(e))
            return False

        if r.status_code != 200:
            self._clock.record_failure()
            self._set_online(False, reason=f"http {r.status_code}")
            return False

        # 服务端响应: {"server_ts": ...}
        # 注意: 响应 body 也需要校验签名 (但本端没有签响应, 由 TLS + PSK 验签)
        # 简化: 信任 TLS, 只用响应里 server_ts
        try:
            data = r.json()
            server_ts = int(data["server_ts"])
        except (ValueError, KeyError):
            self._clock.record_failure()
            return False

        sample = TimeSample(
            server_ts=server_ts,
            agent_ts_before=before,
            agent_ts_after=after,
        )
        self._clock.add_sample(sample)
        self._clock.record_success()
        self._set_online(True)
        return True

    def fetch_poll(self) -> ServerReply:
        """拉取最新配置 (时段) + 待执行指令.

        GET /api/v1/agent/poll
        """
        url = f"{self._cfg.base_url}/api/v1/agent/poll"
        body = b""
        headers = build_signed_request(self._cfg.psk.encode("utf-8"), body)
        try:
            r = self._session.get(
                url, params={"classroom": self._cid}, headers=headers,
                timeout=self._cfg.request_timeout_sec,
            )
        except requests.RequestException as e:
            self._set_online(False, reason=str(e))
            return ServerReply(False, {}, error=str(e))

        if r.status_code != 200:
            self._set_online(False, reason=f"http {r.status_code}")
            return ServerReply(False, {}, error=f"http {r.status_code}")

        try:
            data = r.json()
        except ValueError as e:
            return ServerReply(False, {}, error=f"bad json: {e}")

        self._set_online(True)
        return ServerReply(True, data)

    def send_event(self, payload: bytes) -> bool:
        """上报单个事件 (管理端会同时记录并广播给控制台)."""
        url = f"{self._cfg.base_url}/api/v1/agent/event"
        headers = build_signed_request(self._cfg.psk.encode("utf-8"), payload)
        try:
            r = self._session.post(
                url, data=payload, headers=headers,
                timeout=self._cfg.request_timeout_sec,
            )
        except requests.RequestException as e:
            self._set_online(False, reason=str(e))
            return False
        if r.status_code not in (200, 201, 202):
            self._set_online(False, reason=f"http {r.status_code}")
            return False
        self._set_online(True)
        return True

    def send_log_batch(self, items: list[dict[str, Any]]) -> bool:
        """批量上传日志."""
        if not items:
            return True
        url = f"{self._cfg.base_url}/api/v1/agent/log_batch"
        import json
        body = json.dumps({
            "classroom": self._cid,
            "items": items,
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = build_signed_request(self._cfg.psk.encode("utf-8"), body)
        try:
            r = self._session.post(
                url, data=body, headers=headers,
                timeout=self._cfg.request_timeout_sec,
            )
        except requests.RequestException as e:
            self._set_online(False, reason=str(e))
            return False
        if r.status_code not in (200, 201, 202):
            self._set_online(False, reason=f"http {r.status_code}")
            return False
        self._set_online(True)
        return True

    def upload_log_ring(self) -> int:
        """上传并清空内存环形缓冲."""
        items = drain_ring(limit=200)
        if not items:
            return 0
        ok = self.send_log_batch(items)
        return len(items) if ok else 0

    # ------------------------------------------------------------------ priv
    def _set_online(self, online: bool, reason: str = "") -> None:
        prev = self._online
        self._online = online
        if online and not prev:
            log_event(self._log, "net_recover", detail={"reason": reason})
        elif not online and prev:
            log_event(self._log, "net_lost", detail={"reason": reason})

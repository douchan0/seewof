"""/api/v1/agent/* - 教室端调用的 API.

所有请求必须带 HMAC 签名, 否则 401.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from common.crypto import (
    SIGNATURE_HEADER, TIMESTAMP_HEADER, NONCE_HEADER,
    verify_signed_request,
)

from .. import models, schemas
from ..db import get_db

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
_log = logging.getLogger("seewof.server")


# ---------------------------------------------------------------------------
# 签名校验依赖
# ---------------------------------------------------------------------------
def _classroom_or_401(cid: str, body: bytes, headers: dict[str, str], db: Session):
    """根据 classroom_id 查找 PSK 并校验签名."""
    cls = db.get(models.Classroom, cid)
    if not cls:
        raise HTTPException(status_code=401, detail="unknown classroom")
    # 关键: HTTP header 名称是大小写不敏感的, 我们统一查找
    sig = next((v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER.lower()), "")
    ts = next((v for k, v in headers.items() if k.lower() == TIMESTAMP_HEADER.lower()), "")
    nonce = next((v for k, v in headers.items() if k.lower() == NONCE_HEADER.lower()), "")
    norm = {
        SIGNATURE_HEADER: sig,
        TIMESTAMP_HEADER: ts,
        NONCE_HEADER: nonce,
    }
    try:
        verify_signed_request(
            cls.psk.encode("utf-8"), body, norm,
            replay_window=300,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"signature: {e}")
    return cls


# ---------------------------------------------------------------------------
# 1. 时间同步
# ---------------------------------------------------------------------------
@router.get("/time")
def get_time(
    request: Request,
    db: Session = Depends(get_db),
):
    classroom = request.query_params.get("classroom", "")
    if not classroom:
        raise HTTPException(status_code=400, detail="classroom required")
    body = b""
    _classroom_or_401(classroom, body, {k: v for k, v in request.headers.items()}, db)
    return {"server_ts": int(time.time())}


# ---------------------------------------------------------------------------
# 2. 心跳 / 拉取 (时段 + 远程指令)
# ---------------------------------------------------------------------------
@router.get("/poll", response_model=schemas.PollOut)
def poll(
    request: Request,
    db: Session = Depends(get_db),
):
    classroom = request.query_params.get("classroom", "")
    if not classroom:
        raise HTTPException(status_code=400, detail="classroom required")
    cls = _classroom_or_401(classroom, b"", {k: v for k, v in request.headers.items()}, db)
    # 更新 last_seen
    cls.last_seen = datetime.utcnow()
    cls.online = True
    db.commit()

    # 时段 (默认 schedule)
    sch = db.query(models.Schedule).filter_by(
        classroom_id=classroom, name="default",
    ).first()
    slots = sch.slots() if sch else []

    # 远程指令: 找一条未过期且未消费的
    now = datetime.utcnow()
    cmd = (
        db.query(models.RemoteCommand)
        .filter(models.RemoteCommand.classroom_id == classroom)
        .filter(models.RemoteCommand.expires_at > now)
        .order_by(models.RemoteCommand.expires_at.asc())
        .first()
    )
    remote_unlock = None
    if cmd:
        remote_unlock = {
            "command_id": cmd.id,
            "duration_sec": cmd.duration_sec,
            "issued_at": int(cmd.issued_at.timestamp()),
            "expires_at": int(cmd.expires_at.timestamp()),
            "issued_by": cmd.issued_by,
            "reason": cmd.reason,
        }
        cmd.consumed = True
        db.commit()

    return schemas.PollOut(
        server_ts=int(time.time()),
        slots=slots,
        remote_unlock=remote_unlock,
    )


# ---------------------------------------------------------------------------
# 3. 事件上报
# ---------------------------------------------------------------------------
@router.post("/event")
async def post_event(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.body()
    try:
        obj = json.loads(body)
        payload = schemas.EventIn.model_validate(obj)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad json: {e}")
    _classroom_or_401(payload.classroom, body, {k: v for k, v in request.headers.items()}, db)

    ev = models.EventLog(
        classroom_id=payload.classroom,
        event=payload.event,
        source=payload.source,
        agent_ts=datetime.utcfromtimestamp(payload.agent_ts),
        server_ts=datetime.utcnow(),
        detail_json=json.dumps(payload.detail, ensure_ascii=False),
    )
    db.add(ev)
    # 更新教室状态
    cls = db.get(models.Classroom, payload.classroom)
    if cls:
        cls.last_seen = datetime.utcnow()
        if payload.event in ("lock",):
            cls.locked = True
        elif payload.event in ("unlock",):
            cls.locked = False
        cls.current_state = payload.source or payload.event
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 4. 批量日志上传
# ---------------------------------------------------------------------------
@router.post("/log_batch")
async def post_log_batch(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.body()
    try:
        obj = json.loads(body)
        payload = schemas.LogBatchIn.model_validate(obj)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"bad json: {e}")
    _classroom_or_401(payload.classroom, body, {k: v for k, v in request.headers.items()}, db)

    for it in payload.items:
        ev = models.EventLog(
            classroom_id=payload.classroom,
            event=it.event,
            source=it.source,
            agent_ts=datetime.utcfromtimestamp(it.agent_ts),
            server_ts=datetime.utcnow(),
            detail_json=json.dumps(it.detail, ensure_ascii=False),
        )
        db.add(ev)
    db.commit()
    return {"ok": True, "inserted": len(payload.items)}


# ---------------------------------------------------------------------------
# 5. 心跳 (携带状态摘要)
# ---------------------------------------------------------------------------
@router.post("/heartbeat")
async def post_heartbeat(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.body()
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="bad json")
    classroom = obj.get("classroom", "")
    if not classroom:
        raise HTTPException(status_code=400, detail="classroom required")
    _classroom_or_401(classroom, body, {k: v for k, v in request.headers.items()}, db)

    cls = db.get(models.Classroom, classroom)
    st = db.get(models.ClassroomState, classroom)
    if st is None:
        st = models.ClassroomState(classroom_id=classroom)
        db.add(st)
    st.locked = bool(obj.get("locked", True))
    st.has_usb = bool(obj.get("has_usb", False))
    st.time_drift_sec = int(obj.get("time_drift_sec", 0))
    st.uptime_sec = int(obj.get("uptime_sec", 0))
    st.pending_remote_unlock = int(obj.get("pending_remote_unlock", 0))
    st.updated_at = datetime.utcnow()
    if cls:
        cls.online = True
        cls.last_seen = datetime.utcnow()
        cls.locked = st.locked
    db.commit()
    return {"ok": True}

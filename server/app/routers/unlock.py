"""/api/v1/classrooms/{cid}/unlock - 远程解锁指令."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..db import get_db

router = APIRouter(
    prefix="/api/v1/classrooms/{cid}/unlock", tags=["unlock"],
)


@router.post("", response_model=schemas.RemoteUnlockOut)
def issue_unlock(
    cid: str,
    payload: schemas.RemoteUnlockIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="classroom not found")
    cmd_id = secrets.token_urlsafe(12)
    expires = datetime.utcnow() + timedelta(seconds=payload.duration_sec)
    cmd = models.RemoteCommand(
        id=cmd_id, classroom_id=cid,
        command_type="unlock",
        duration_sec=payload.duration_sec,
        issued_by=user.username,
        reason=payload.reason,
        expires_at=expires,
    )
    db.add(cmd)
    db.commit()
    return schemas.RemoteUnlockOut(
        command_id=cmd_id, classroom_id=cid,
        duration_sec=payload.duration_sec,
        expires_at=expires,
        issued_by=user.username,
        reason=payload.reason,
    )


@router.delete("/{cmd_id}")
def revoke_unlock(
    cid: str,
    cmd_id: str,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    cmd = db.get(models.RemoteCommand, cmd_id)
    if not cmd or cmd.classroom_id != cid:
        raise HTTPException(status_code=404, detail="command not found")
    db.delete(cmd)
    db.commit()
    return {"ok": True}


@router.get("/active", response_model=list[schemas.RemoteUnlockOut])
def list_active(
    cid: str,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    now = datetime.utcnow()
    rows = (
        db.query(models.RemoteCommand)
        .filter(models.RemoteCommand.classroom_id == cid)
        .filter(models.RemoteCommand.expires_at > now)
        .order_by(models.RemoteCommand.expires_at.desc())
        .all()
    )
    return [
        schemas.RemoteUnlockOut(
            command_id=r.id, classroom_id=r.classroom_id,
            duration_sec=r.duration_sec, expires_at=r.expires_at,
            issued_by=r.issued_by, reason=r.reason,
        )
        for r in rows
    ]

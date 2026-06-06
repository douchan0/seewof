"""/api/v1/classrooms - 教室管理."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user, require_admin
from ..db import get_db

router = APIRouter(prefix="/api/v1/classrooms", tags=["classrooms"])


@router.get("", response_model=list[schemas.ClassroomOut])
def list_classrooms(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    rows = db.query(models.Classroom).order_by(models.Classroom.id).all()
    out = []
    for c in rows:
        st = db.get(models.ClassroomState, c.id)
        out.append(schemas.ClassroomOut(
            id=c.id, name=c.name, mac=c.mac, ip=c.ip,
            online=c.online, locked=c.locked,
            current_state=c.current_state,
            last_seen=c.last_seen,
            has_usb=bool(st and st.has_usb),
            time_drift_sec=st.time_drift_sec if st else 0,
            pending_remote_unlock=st.pending_remote_unlock if st else 0,
        ))
    return out


@router.post("", response_model=schemas.ClassroomOut)
def create_classroom(
    payload: schemas.ClassroomIn,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    if db.get(models.Classroom, payload.id):
        raise HTTPException(status_code=409, detail="classroom id exists")
    c = models.Classroom(
        id=payload.id, name=payload.name, mac=payload.mac,
        ip=payload.ip, psk=payload.psk,
    )
    db.add(c)
    # 默认时间表
    db.add(models.Schedule(classroom_id=payload.id, name="default", slots_json="[]"))
    db.commit()
    return schemas.ClassroomOut(
        id=c.id, name=c.name, mac=c.mac, ip=c.ip,
        online=False, locked=True, current_state="unknown",
    )


@router.put("/{cid}", response_model=schemas.ClassroomOut)
def update_classroom(
    cid: str,
    payload: dict,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    for k in ("name", "mac", "ip"):
        if k in payload:
            setattr(c, k, str(payload[k]))
    if "psk" in payload and len(str(payload["psk"])) >= 16:
        c.psk = str(payload["psk"])
    db.commit()
    st = db.get(models.ClassroomState, cid)
    return schemas.ClassroomOut(
        id=c.id, name=c.name, mac=c.mac, ip=c.ip,
        online=c.online, locked=c.locked, current_state=c.current_state,
        last_seen=c.last_seen,
        has_usb=bool(st and st.has_usb),
        time_drift_sec=st.time_drift_sec if st else 0,
        pending_remote_unlock=st.pending_remote_unlock if st else 0,
    )


@router.get("/{cid}", response_model=schemas.ClassroomOut)
def get_classroom(
    cid: str,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    st = db.get(models.ClassroomState, cid)
    return schemas.ClassroomOut(
        id=c.id, name=c.name, mac=c.mac, ip=c.ip,
        online=c.online, locked=c.locked, current_state=c.current_state,
        last_seen=c.last_seen,
        has_usb=bool(st and st.has_usb),
        time_drift_sec=st.time_drift_sec if st else 0,
        pending_remote_unlock=st.pending_remote_unlock if st else 0,
    )


@router.delete("/{cid}")
def delete_classroom(
    cid: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/{cid}/psk/rotate")
def rotate_psk(
    cid: str,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="not found")
    new_psk = secrets.token_urlsafe(48)
    c.psk = new_psk
    db.commit()
    return {"psk": new_psk}

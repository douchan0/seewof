"""/api/v1/classrooms/{cid}/schedule - 时间表."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..db import get_db

router = APIRouter(prefix="/api/v1/classrooms/{cid}/schedule", tags=["schedule"])


def _validate_slots(slots: list[dict]) -> None:
    """简单合理性校验."""
    seen = set()
    for s in slots:
        wd = tuple(sorted(s.get("weekdays", [])))
        if not wd or any(w < 0 or w > 6 for w in wd):
            raise HTTPException(status_code=400, detail="invalid weekdays")
        if wd in seen:
            raise HTTPException(status_code=400, detail="duplicate weekday set")
        seen.add(wd)
        sm = int(s.get("start_min", 0))
        em = int(s.get("end_min", 0))
        if not (0 <= sm <= 1440) or not (0 <= em <= 1440):
            raise HTTPException(status_code=400, detail="invalid time range")
        if sm == em:
            raise HTTPException(status_code=400, detail="empty slot")


@router.get("", response_model=list[schemas.ScheduleOut])
def list_schedules(
    cid: str,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="classroom not found")
    rows = db.query(models.Schedule).filter_by(classroom_id=cid).all()
    return [
        schemas.ScheduleOut(
            id=r.id, name=r.name, slots=r.slots(), updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("", response_model=schemas.ScheduleOut)
def upsert_schedule(
    cid: str,
    payload: schemas.ScheduleIn,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    c = db.get(models.Classroom, cid)
    if not c:
        raise HTTPException(status_code=404, detail="classroom not found")
    slots = [s.model_dump() for s in payload.slots]
    _validate_slots(slots)
    row = db.query(models.Schedule).filter_by(
        classroom_id=cid, name=payload.name,
    ).first()
    if row:
        row.set_slots(slots)
    else:
        row = models.Schedule(classroom_id=cid, name=payload.name)
        row.set_slots(slots)
        db.add(row)
    db.commit()
    db.refresh(row)
    return schemas.ScheduleOut(
        id=row.id, name=row.name, slots=row.slots(), updated_at=row.updated_at,
    )


@router.delete("/{name}")
def delete_schedule(
    cid: str,
    name: str,
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    row = db.query(models.Schedule).filter_by(
        classroom_id=cid, name=name,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="schedule not found")
    db.delete(row)
    db.commit()
    return {"ok": True}

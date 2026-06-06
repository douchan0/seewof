"""/api/v1/logs - 事件日志查询."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user
from ..db import get_db

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


@router.get("", response_model=schemas.LogPage)
def list_logs(
    classroom: str | None = Query(None),
    event: str | None = Query(None),
    q: str | None = Query(None, description="在 detail 中模糊搜索"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    query = db.query(models.EventLog)
    if classroom:
        query = query.filter(models.EventLog.classroom_id == classroom)
    if event:
        query = query.filter(models.EventLog.event == event)
    if q:
        query = query.filter(models.EventLog.detail_json.contains(q))
    total = query.count()
    rows = (
        query.order_by(models.EventLog.server_ts.desc())
        .offset(offset).limit(limit).all()
    )
    items = [
        schemas.LogItem(
            id=r.id,
            classroom_id=r.classroom_id,
            event=r.event,
            source=r.source,
            agent_ts=r.agent_ts,
            server_ts=r.server_ts,
            detail=r.detail(),
        )
        for r in rows
    ]
    return schemas.LogPage(total=total, items=items)


@router.get("/events")
def distinct_events(
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    """返回出现过的事件类型列表 (前端下拉)."""
    rows = (
        db.query(models.EventLog.event)
        .distinct().order_by(models.EventLog.event).all()
    )
    return [r[0] for r in rows]

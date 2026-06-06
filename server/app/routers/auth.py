"""/api/v1/auth - 登录与当前用户."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import (
    create_token, get_current_user, hash_password, verify_password,
)
from ..db import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginOut)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter_by(username=form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token, ttl = create_token(user)
    return schemas.LoginOut(
        access_token=token,
        expires_in=ttl,
        user=schemas.UserOut.model_validate(user),
    )


@router.post("/change_password")
def change_password(
    payload: dict,
    me: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    old = payload.get("old_password", "")
    new = payload.get("new_password", "")
    if not verify_password(old, me.password_hash):
        raise HTTPException(status_code=400, detail="old password invalid")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="new password too short")
    me.password_hash = hash_password(new)
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.post("/bootstrap_admin")
def bootstrap_admin(
    payload: dict,
    db: Session = Depends(get_db),
):
    """首次启动: 无用户时, 用 SEEWOF_BOOTSTRAP_TOKEN 创建管理员."""
    import os
    token = os.environ.get("SEEWOF_BOOTSTRAP_TOKEN", "")
    if not token or payload.get("token") != token:
        raise HTTPException(status_code=403, detail="bad bootstrap token")
    if db.query(models.User).count() > 0:
        raise HTTPException(status_code=409, detail="already initialized")
    username = payload.get("username", "admin")
    password = payload.get("password", "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password too short")
    u = models.User(
        username=username, password_hash=hash_password(password),
        display_name="Administrator", role="admin",
    )
    db.add(u)
    db.commit()
    return {"ok": True, "username": username}

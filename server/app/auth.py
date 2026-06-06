"""认证: JWT + bcrypt."""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .db import get_db
from . import models

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
JWT_SECRET = os.environ.get("SEEWOF_JWT_SECRET", "change-me-in-production-please")
JWT_ALGO = "HS256"
TOKEN_TTL_SEC = 8 * 3600

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def hash_password(plain: str) -> str:
    return pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)


def create_token(user: models.User) -> tuple[str, int]:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SEC,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO), TOKEN_TTL_SEC


def get_current_user(
    token: str | None = Depends(oauth2),
    db: Session = Depends(get_db),
) -> models.User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing token",
        )
    try:
        payload: dict[str, Any] = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        uid = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token")

    user = db.get(models.User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="user not found")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    return user

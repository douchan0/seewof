"""/api/v1/usb - U 盘授权管理."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from common.crypto import (
    UsbKeyPayload, rsa_sign, encode_signature, pack_teacher_key,
)
from common.protocol import EventType

from .. import models, schemas
from ..auth import get_current_user, require_admin
from ..db import get_db
from ..models import EventLog, UsbKey

router = APIRouter(prefix="/api/v1/usb", tags=["usb"])


# 私钥路径: 启动时自动生成
def _key_paths() -> tuple[Path, Path]:
    base = Path("data")
    base.mkdir(parents=True, exist_ok=True)
    return base / "private.pem", base / "public.pem"


def _ensure_keys() -> tuple[bytes, bytes]:
    """确保 RSA 密钥对存在, 返回 (private_pem, public_pem)."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    priv_p, pub_p = _key_paths()
    if priv_p.exists() and pub_p.exists():
        return priv_p.read_bytes(), pub_p.read_bytes()

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_p.write_bytes(priv_pem)
    pub_p.write_bytes(pub_pem)
    return priv_pem, pub_pem


@router.get("/public_key")
def get_public_key(_user: models.User = Depends(get_current_user)):
    _priv, pub_pem = _ensure_keys()
    return Response(content=pub_pem, media_type="application/x-pem-file")


@router.get("", response_model=list[schemas.UsbKeyOut])
def list_keys(
    db: Session = Depends(get_db),
    _user: models.User = Depends(get_current_user),
):
    return db.query(models.UsbKey).order_by(models.UsbKey.id.desc()).all()


@router.post("", response_model=schemas.UsbKeyOut)
def add_key(
    payload: schemas.UsbKeyIn,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    if db.query(models.UsbKey).filter_by(serial=payload.serial).first():
        raise HTTPException(status_code=409, detail="serial exists")
    k = models.UsbKey(
        serial=payload.serial,
        teacher_id=payload.teacher_id,
        teacher_name=payload.teacher_name,
        expires_at=payload.expires_at,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    return k


@router.post("/{kid}/sign")
def sign_key(
    kid: int,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    """为 U 盘签发 teacher.key, 直接返回文件内容."""
    k = db.get(models.UsbKey, kid)
    if not k:
        raise HTTPException(status_code=404, detail="not found")
    if k.revoked:
        raise HTTPException(status_code=400, detail="key revoked")

    priv_pem, _ = _ensure_keys()
    from cryptography.hazmat.primitives import serialization
    from common.crypto import load_private_key
    priv = load_private_key(priv_pem)

    payload_obj = UsbKeyPayload(
        serial=k.serial,
        teacher_id=k.teacher_id,
        teacher_name=k.teacher_name,
        issued_at=int(datetime.utcnow().timestamp()),
        expires_at=int(k.expires_at.timestamp()) if k.expires_at else 0,
        nonce=secrets.token_hex(8),
    )
    sig = rsa_sign(priv, payload_obj.canonical_json())
    blob = pack_teacher_key(payload_obj, sig)
    return Response(
        content=blob, media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="teacher.key"',
        },
    )


@router.post("/{kid}/revoke")
def revoke_key(
    kid: int,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    k = db.get(models.UsbKey, kid)
    if not k:
        raise HTTPException(status_code=404, detail="not found")
    k.revoked = True
    db.commit()
    return {"ok": True}


@router.delete("/{kid}")
def delete_key(
    kid: int,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    k = db.get(models.UsbKey, kid)
    if not k:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(k)
    db.commit()
    return {"ok": True}

"""共享加密工具.

提供:
- HMAC-SHA256 消息签名 (教室端 <-> 管理端 通信防伪)
- RSA-2048 签名/验签 (U盘 teacher.key 验证)
- 预共享密钥 (PSK) 派生

设计原则:
- 单一职责: 仅做加密原语, 不掺杂业务逻辑.
- 无副作用: 所有函数纯函数, 便于测试.
- 失败安全: 验签失败抛出明确的异常, 由调用方决定如何处理.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
NONCE_LEN = 16
HMAC_ALGO = hashlib.sha256
SIGNATURE_HEADER = "X-Seewof-Signature"
TIMESTAMP_HEADER = "X-Seewof-Timestamp"
NONCE_HEADER = "X-Seewof-Nonce"

# 重放窗口: 默认 5 分钟
DEFAULT_REPLAY_WINDOW = 300


# ---------------------------------------------------------------------------
# HMAC 签名 (教室端 <-> 管理端 通信)
# ---------------------------------------------------------------------------
def hmac_sign(secret: bytes, payload: bytes) -> str:
    """对 payload 字节做 HMAC-SHA256, 返回 base64 字符串."""
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
        raise ValueError("secret must be bytes with length >= 16")
    mac = hmac.new(secret, payload, HMAC_ALGO)
    return base64.b64encode(mac.digest()).decode("ascii")


def hmac_verify(secret: bytes, payload: bytes, signature_b64: str) -> bool:
    """验证 HMAC 签名, 使用 constant-time 比较."""
    try:
        expected = hmac_sign(secret, payload)
        return hmac.compare_digest(expected, signature_b64)
    except (ValueError, TypeError):
        return False


def build_signed_request(
    secret: bytes,
    body: bytes,
    timestamp: int | None = None,
) -> dict[str, str]:
    """构造带签名的 HTTP 头.

    返回可注入到 requests 的 headers 字典.
    """
    ts = int(time.time()) if timestamp is None else timestamp
    nonce = base64.b64encode(
        hashlib.sha256(f"{ts}-{time.time_ns()}".encode()).digest()[:NONCE_LEN]
    ).decode("ascii")

    # 签名内容: timestamp + nonce + body
    msg = f"{ts}.{nonce}.".encode("utf-8") + body
    sig = hmac_sign(secret, msg)

    return {
        TIMESTAMP_HEADER: str(ts),
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: sig,
    }


def verify_signed_request(
    secret: bytes,
    body: bytes,
    headers: dict[str, str],
    *,
    replay_window: int = DEFAULT_REPLAY_WINDOW,
    now: int | None = None,
) -> None:
    """验证签名 + 防重放. 失败抛出 ValueError."""
    sig = headers.get(SIGNATURE_HEADER, "")
    ts_raw = headers.get(TIMESTAMP_HEADER, "")
    nonce = headers.get(NONCE_HEADER, "")

    if not sig or not ts_raw or not nonce:
        raise ValueError("missing signature headers")

    try:
        ts = int(ts_raw)
    except ValueError as e:
        raise ValueError("invalid timestamp") from e

    cur = int(time.time()) if now is None else now
    if abs(cur - ts) > replay_window:
        raise ValueError(f"timestamp out of window: {ts} vs {cur}")

    msg = f"{ts}.{nonce}.".encode("utf-8") + body
    if not hmac_verify(secret, msg, sig):
        raise ValueError("hmac signature mismatch")


# ---------------------------------------------------------------------------
# RSA 签名 (U盘 teacher.key)
# ---------------------------------------------------------------------------
def load_public_key(pem: bytes | str) -> rsa.RSAPublicKey:
    """从 PEM 加载公钥."""
    if isinstance(pem, str):
        pem = pem.encode("utf-8")
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError("expected RSA public key")
    return key


def load_private_key(pem: bytes | str, password: bytes | None = None) -> rsa.RSAPrivateKey:
    """从 PEM 加载私钥 (仅管理端签发U盘时使用)."""
    if isinstance(pem, str):
        pem = pem.encode("utf-8")
    key = serialization.load_pem_private_key(pem, password=password)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("expected RSA private key")
    return key


def rsa_sign(private_key: rsa.RSAPrivateKey, data: bytes) -> bytes:
    """RSA-PSS 签名."""
    return private_key.sign(
        data,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )


def rsa_verify(public_key: rsa.RSAPublicKey, signature: bytes, data: bytes) -> bool:
    """RSA-PSS 验签, 失败返回 False (不抛异常)."""
    try:
        public_key.verify(
            signature,
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False


def encode_signature(sig: bytes) -> str:
    return base64.b64encode(sig).decode("ascii")


def decode_signature(sig_b64: str) -> bytes:
    return base64.b64decode(sig_b64)


# ---------------------------------------------------------------------------
# U盘 teacher.key 文件结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UsbKeyPayload:
    """teacher.key 内嵌的 payload (验签通过后才信任)."""

    serial: str           # USB 设备硬件序列号
    teacher_id: str       # 教师工号
    teacher_name: str     # 教师姓名
    issued_at: int        # 签发 unix 时间 (秒)
    expires_at: int       # 过期 unix 时间 (秒), 0 表示永不过期
    nonce: str            # 随机串, 防相同 payload 产生相同签名

    def canonical_json(self) -> bytes:
        """规范化 JSON 字节 (字段顺序固定, 用于签名/验签)."""
        obj = {
            "serial": self.serial,
            "teacher_id": self.teacher_id,
            "teacher_name": self.teacher_name,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }
        return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def is_expired(self, now: int | None = None) -> bool:
        if self.expires_at <= 0:
            return False
        cur = int(time.time()) if now is None else now
        return cur > self.expires_at


def pack_teacher_key(payload: UsbKeyPayload, signature: bytes) -> bytes:
    """打包 teacher.key 文件内容 = payload_json + '.' + base64(sig)."""
    return payload.canonical_json() + b"." + encode_signature(signature).encode("ascii")


def unpack_teacher_key(data: bytes) -> tuple[UsbKeyPayload, bytes]:
    """解析 teacher.key. 失败抛出 ValueError."""
    if b"." not in data:
        raise ValueError("invalid teacher.key format")
    payload_json, _, sig_b64 = data.rpartition(b".")
    try:
        obj: Any = json.loads(payload_json)
        payload = UsbKeyPayload(
            serial=str(obj["serial"]),
            teacher_id=str(obj["teacher_id"]),
            teacher_name=str(obj["teacher_name"]),
            issued_at=int(obj["issued_at"]),
            expires_at=int(obj["expires_at"]),
            nonce=str(obj["nonce"]),
        )
        return payload, decode_signature(sig_b64.decode("ascii"))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        raise ValueError(f"invalid teacher.key payload: {e}") from e


# ---------------------------------------------------------------------------
# 工具: 生成 PSK
# ---------------------------------------------------------------------------
def generate_psk(length: int = 48) -> str:
    """生成预共享密钥, base64 编码字符串."""
    import secrets  # 延迟导入, 仅在需要时
    raw = secrets.token_bytes(length)
    return base64.b64encode(raw).decode("ascii")

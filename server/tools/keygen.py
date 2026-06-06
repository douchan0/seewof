"""U盘签发工具 (CLI).

功能:
- 生成 RSA 密钥对
- 输入 U 盘序列号 + 教师信息, 输出 teacher.key 文件

用法:
    python -m server.tools.keygen init-keys --out data/
    python -m server.tools.keygen issue \
        --private data/private.pem \
        --serial "USBSTOR\\DISK&VEN_Kingston&PROD_DataTraveler_3.0\\ABCDEF123456" \
        --teacher-id T001 --teacher-name "张老师" \
        --out ./teacher.key

在 Linux 上无法直接读取 Windows 的 USB 硬件 ID,
所以一般在管理端网页上, 教室端用 `python -m agent.usbdiag --drive E` 提取序列号,
然后粘贴到管理端.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from common.crypto import (
    UsbKeyPayload, rsa_sign, encode_signature, pack_teacher_key,
    load_private_key,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def cmd_init_keys(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
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
    (out / "private.pem").write_bytes(priv_pem)
    (out / "public.pem").write_bytes(pub_pem)
    print(f"wrote {out/'private.pem'} (KEEP SECRET!)")
    print(f"wrote {out/'public.pem'} (deploy to agent)")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    priv_pem = Path(args.private).read_bytes()
    priv = load_private_key(priv_pem)
    expires_at = 0
    if args.days:
        expires_at = int(
            (datetime.utcnow() + timedelta(days=args.days)).timestamp()
        )
    payload = UsbKeyPayload(
        serial=args.serial,
        teacher_id=args.teacher_id,
        teacher_name=args.teacher_name,
        issued_at=int(datetime.utcnow().timestamp()),
        expires_at=expires_at,
        nonce=secrets.token_hex(8),
    )
    sig = rsa_sign(priv, payload.canonical_json())
    blob = pack_teacher_key(payload, sig)
    out = Path(args.out)
    out.write_bytes(blob)
    print(f"wrote {out} ({len(blob)} bytes)")
    print(f"  serial       : {payload.serial}")
    print(f"  teacher_id   : {payload.teacher_id}")
    print(f"  teacher_name : {payload.teacher_name}")
    print(f"  expires_at   : {expires_at or 'never'}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from common.crypto import unpack_teacher_key, rsa_verify, load_public_key
    data = Path(args.file).read_bytes()
    try:
        payload, sig = unpack_teacher_key(data)
    except ValueError as e:
        print(f"invalid teacher.key: {e}", file=sys.stderr)
        return 1
    info = {
        "serial": payload.serial,
        "teacher_id": payload.teacher_id,
        "teacher_name": payload.teacher_name,
        "issued_at": payload.issued_at,
        "expires_at": payload.expires_at,
        "nonce": payload.nonce,
        "expired": payload.is_expired(),
    }
    if args.public:
        pub = load_public_key(Path(args.public).read_bytes())
        info["signature_valid"] = rsa_verify(pub, sig, payload.canonical_json())
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Seewof U 盘签名工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("init-keys", help="生成 RSA 密钥对")
    p1.add_argument("--out", default="data", help="输出目录")
    p1.set_defaults(fn=cmd_init_keys)

    p2 = sub.add_parser("issue", help="签发 teacher.key")
    p2.add_argument("--private", required=True, help="私钥 PEM 路径")
    p2.add_argument("--serial", required=True, help="U 盘硬件序列号")
    p2.add_argument("--teacher-id", required=True)
    p2.add_argument("--teacher-name", required=True)
    p2.add_argument("--days", type=int, default=0,
                    help="有效天数, 0=永不过期")
    p2.add_argument("--out", default="teacher.key", help="输出文件")
    p2.set_defaults(fn=cmd_issue)

    p3 = sub.add_parser("show", help="解析并验证 teacher.key")
    p3.add_argument("file")
    p3.add_argument("--public", help="可选: 验证签名")
    p3.set_defaults(fn=cmd_show)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

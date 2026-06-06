"""加密 / 签名单元测试."""

from __future__ import annotations

import base64
import time
import unittest

from common.crypto import (
    HMAC_ALGO, NONCE_HEADER, SIGNATURE_HEADER, TIMESTAMP_HEADER, UsbKeyPayload,
    build_signed_request, decode_signature, encode_signature, generate_psk,
    hmac_sign, hmac_verify, pack_teacher_key, rsa_sign, rsa_verify,
    unpack_teacher_key, verify_signed_request,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


class HmacTest(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        s = b"x" * 32
        body = b"hello world"
        sig = hmac_sign(s, body)
        self.assertTrue(hmac_verify(s, body, sig))

    def test_verify_tamper(self):
        s = b"x" * 32
        sig = hmac_sign(s, b"a")
        self.assertFalse(hmac_verify(s, b"b", sig))

    def test_secret_too_short(self):
        with self.assertRaises(ValueError):
            hmac_sign(b"short", b"x")

    def test_signed_request_headers(self):
        s = b"x" * 32
        h = build_signed_request(s, b"{}", timestamp=1700000000)
        self.assertEqual(h[TIMESTAMP_HEADER], "1700000000")
        self.assertIn(NONCE_HEADER, h)
        self.assertIn(SIGNATURE_HEADER, h)

    def test_signed_request_verify_ok(self):
        s = b"x" * 32
        body = b'{"k":1}'
        h = build_signed_request(s, body, timestamp=1700000000)
        # 传入 now, 避免边界问题
        verify_signed_request(s, body, h, now=1700000001)

    def test_signed_request_replay(self):
        s = b"x" * 32
        body = b'{"k":1}'
        h = build_signed_request(s, body, timestamp=1700000000)
        with self.assertRaises(ValueError):
            verify_signed_request(s, body, h, replay_window=10, now=1700001000)

    def test_signed_request_signature_tamper(self):
        s = b"x" * 32
        body = b'{"k":1}'
        h = build_signed_request(s, body, timestamp=1700000000)
        h[SIGNATURE_HEADER] = "AAAA"
        with self.assertRaises(ValueError):
            verify_signed_request(s, body, h, now=1700000000)


class RsaTest(unittest.TestCase):
    def setUp(self):
        self.priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pub = self.priv.public_key()

    def test_sign_verify(self):
        sig = rsa_sign(self.priv, b"hello")
        self.assertTrue(rsa_verify(self.pub, sig, b"hello"))
        self.assertFalse(rsa_verify(self.pub, sig, b"hellp"))

    def test_encode_decode(self):
        sig = rsa_sign(self.priv, b"x")
        b = encode_signature(sig)
        self.assertEqual(decode_signature(b), sig)


class TeacherKeyTest(unittest.TestCase):
    def setUp(self):
        self.priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pub = self.priv.public_key()

    def test_pack_unpack_roundtrip(self):
        p = UsbKeyPayload(
            serial="USBXXX", teacher_id="T1", teacher_name="张",
            issued_at=int(time.time()), expires_at=0, nonce="abc",
        )
        sig = rsa_sign(self.priv, p.canonical_json())
        blob = pack_teacher_key(p, sig)
        p2, sig2 = unpack_teacher_key(blob)
        self.assertEqual(p.serial, p2.serial)
        self.assertEqual(p.canonical_json(), p2.canonical_json())
        self.assertEqual(sig, sig2)
        self.assertTrue(rsa_verify(self.pub, sig2, p2.canonical_json()))

    def test_expired(self):
        p = UsbKeyPayload(
            serial="X", teacher_id="T1", teacher_name="n",
            issued_at=100, expires_at=200, nonce="n",
        )
        self.assertTrue(p.is_expired(now=300))
        self.assertFalse(p.is_expired(now=199))
        # 0 = 永不过期
        p2 = UsbKeyPayload(serial="X", teacher_id="T1", teacher_name="n",
                           issued_at=100, expires_at=0, nonce="n")
        self.assertFalse(p2.is_expired(now=10**10))

    def test_unpack_invalid(self):
        with self.assertRaises(ValueError):
            unpack_teacher_key(b"no_dot_separator")


class PskTest(unittest.TestCase):
    def test_generate(self):
        s = generate_psk()
        self.assertGreaterEqual(len(base64.b64decode(s)), 32)


if __name__ == "__main__":
    unittest.main()

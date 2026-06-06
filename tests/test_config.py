"""配置加载测试."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.config import AgentConfig, ConfigError


class ConfigTest(unittest.TestCase):
    def _write(self, data: dict) -> str:
        f = tempfile.NamedTemporaryFile(
            "w", delete=False, suffix=".json", encoding="utf-8",
        )
        json.dump(data, f)
        f.close()
        return f.name

    def test_minimal_valid(self):
        p = self._write({
            "classroom_id": "ROOM-1",
            "server": {"base_url": "https://10.0.0.1:8443",
                       "psk": "x" * 48},
        })
        cfg = AgentConfig.load(p)
        self.assertEqual(cfg.classroom_id, "ROOM-1")
        self.assertEqual(cfg.server.base_url, "https://10.0.0.1:8443")
        self.assertTrue(cfg.lock.block_keyboard)

    def test_psk_too_short(self):
        p = self._write({
            "classroom_id": "ROOM-1",
            "server": {"base_url": "https://10.0.0.1:8443",
                       "psk": "short"},
        })
        with self.assertRaises(ConfigError):
            AgentConfig.load(p)

    def test_missing_classroom_id(self):
        p = self._write({
            "server": {"base_url": "https://10.0.0.1:8443",
                       "psk": "x" * 48},
        })
        with self.assertRaises(ConfigError):
            AgentConfig.load(p)

    def test_missing_server(self):
        p = self._write({"classroom_id": "ROOM-1"})
        with self.assertRaises(ConfigError):
            AgentConfig.load(p)

    def test_garbage(self):
        p = self._write({"this": "is", "not": "valid"})
        with self.assertRaises(ConfigError):
            AgentConfig.load(p)

    def test_default_lock(self):
        p = self._write({
            "classroom_id": "ROOM-1",
            "server": {"base_url": "https://x", "psk": "x" * 48},
        })
        cfg = AgentConfig.load(p)
        self.assertEqual(cfg.lock.usb_remove_grace_sec, 5)
        self.assertEqual(cfg.protection.service_name, "SeewofAgent")


if __name__ == "__main__":
    unittest.main()

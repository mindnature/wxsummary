"""Synthetic connection and refresh tests; never operate on real WeChat."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wechat_summary.config import load_config
from wechat_summary.demo_data import build
from wechat_summary.errors import WSError
from wechat_summary.setup import find_data_roots, run_setup, save_connection
from wechat_summary.sources.refresh import run_refresh


class ConnectionTests(unittest.TestCase):
    def test_manual_mirror_sets_default_without_extraction(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            mirror = build(base / "demo", "2026-10-19")
            cfg_file = base / "config.yaml"
            args = SimpleNamespace(config=str(cfg_file), tool_dir=str(base / "tool"),
                                   use_mirror=str(mirror), account=None, wechat_data=None,
                                   status=False, install_tool=False, extract_key=False)
            result, code = run_setup(load_config(), args)
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "ready")
            cfg = load_config(str(cfg_file))
            self.assertEqual(cfg["source"]["type"], "decrypted_dir")
            self.assertEqual(Path(cfg["source"]["path"]), mirror.resolve())
            self.assertEqual(cfg["refresh"]["command"], [])

    def test_manual_mirror_preserves_groups_and_json_config(self):
        with tempfile.TemporaryDirectory() as root:
            conf = Path(root) / "config.json"
            conf.write_text(json.dumps({"group_sets": {"要保留": ["g@chatroom"]},
                                        "me": {"names": ["我"]}}), encoding="utf-8")
            save_connection(conf, source={"type": "decrypted_dir", "path": "A"})
            cfg = load_config(str(conf))
            self.assertEqual(cfg["group_sets"]["要保留"], ["g@chatroom"])
            self.assertEqual(cfg["me"]["names"], ["我"])
            self.assertEqual(cfg["source"]["path"], "A")

    def test_setup_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(config=None, tool_dir=str(Path(root) / "tool"),
                                   use_mirror=None, account=None, wechat_data=None,
                                   status=True, install_tool=False, extract_key=False)
            with patch("wechat_summary.setup.find_data_roots", return_value=[]):
                result, code = run_setup(load_config(), args)
            self.assertEqual(code, 2)
            self.assertEqual(result["code"], "wechat_data_missing")
            self.assertFalse((Path(root) / "tool").exists())

    def test_detect_fake_wechat_root(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            db = home / "Documents" / "xwechat_files" / "x123" / "db_storage" / "message" / "message_0.db"
            db.parent.mkdir(parents=True)
            db.write_bytes(b"encrypted")
            roots = find_data_roots(home=home, environ={})
            self.assertEqual(roots, [(home / "Documents" / "xwechat_files").resolve()])

    def test_refresh_rejects_arbitrary_commands(self):
        for bad in (["cmd", "/c", "whoami"], "whoami", ["python", "evil.py"]):
            with self.subTest(bad=bad):
                with self.assertRaises(WSError):
                    run_refresh({"refresh": {"command": bad, "cwd": "."}})

    def test_refresh_allowlisted_shape_and_scrubs_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            tool = Path(root)
            script = tool / "scripts" / "windows" / "decrypt_all.py"
            script.parent.mkdir(parents=True)
            script.write_text("pass", encoding="utf-8")
            py = tool / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            py.parent.mkdir(parents=True)
            py.write_text("dummy", encoding="utf-8")
            cfg = {"refresh": {"command": [str(py), "scripts/windows/decrypt_all.py"],
                               "cwd": str(tool), "timeout_seconds": 30}}
            proc = SimpleNamespace(returncode=0, stdout="completed " + "A" * 64, stderr="")
            with patch("wechat_summary.sources.refresh.subprocess.run", return_value=proc) as run:
                result = run_refresh(cfg)
            self.assertTrue(result["ok"])
            self.assertIn("[redacted]", result["output_tail"])
            self.assertNotIn("A" * 64, result["output_tail"])
            self.assertFalse(run.call_args.kwargs["shell"])

    def test_refresh_unconfigured(self):
        with self.assertRaises(WSError) as cm:
            run_refresh(load_config())
        self.assertEqual(cm.exception.code, "refresh_not_configured")


if __name__ == "__main__":
    unittest.main()

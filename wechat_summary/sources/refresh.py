"""On-demand, user-configured external plaintext mirror refresh.

Runs only the audited shape: isolated Python executable + the external project's
scripts/windows/decrypt_all.py in its existing directory. No shell interpolation.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ..errors import WSError

SECRET = re.compile(r"\b[0-9a-fA-F]{32,}\b")
SCRIPT = "scripts/windows/decrypt_all.py"


def run_refresh(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("refresh") or {}
    command = settings.get("command")
    if not isinstance(command, list) or len(command) != 2:
        raise WSError("refresh_not_configured", "未配置安全的自动刷新命令；请先完成 setup，或手动提供明文镜像")
    py, script = map(str, command)
    cwd = Path(settings.get("cwd") or "").expanduser().resolve()
    python_path = Path(py).expanduser().resolve()
    if script.replace("\\", "/") != SCRIPT or not (cwd / SCRIPT).is_file():
        raise WSError("refresh_not_configured", "拒绝执行未识别的刷新脚本")
    expected_python = cwd / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python_path != expected_python.resolve() or not python_path.is_file():
        raise WSError("refresh_not_configured", "刷新程序必须使用外部工具目录内的隔离 Python 环境")
    timeout = max(1, min(1800, int(settings.get("timeout_seconds") or 1800)))
    started = time.monotonic()
    try:
        proc = subprocess.run([str(python_path), SCRIPT], cwd=str(cwd),
                              shell=False, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WSError("refresh_failed", f"微信明文镜像刷新失败：{type(exc).__name__}") from exc
    stdout = SECRET.sub("[redacted]", (proc.stdout or "")[-1000:])
    stderr = SECRET.sub("[redacted]", (proc.stderr or "")[-400:])
    if proc.returncode:
        raise WSError("refresh_failed", f"外部刷新程序退出码 {proc.returncode}",
                      details={"output_tail": stdout, "error_tail": stderr})
    return {"ok": True, "status": "refreshed", "elapsed_seconds": round(time.monotonic() - started, 1),
            "output_tail": stdout}

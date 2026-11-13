"""Opt-in Windows WeChat 4.x connection wizard.

Never silently starts key extraction, terminates WeChat, uploads messages, or
modifies an encrypted database. Uses a separate, user-approved open-source tool.
"""
from __future__ import annotations

import copy
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .config import load_text_config, resolve_config_path
from .errors import WSError
from .sources import open_source
from .sources.refresh import run_refresh

TOOL_REPO = "https://github.com/WeinanHu/wechat-decrypt.git"
TOOL_DEFAULT = Path("~/.wxsummary/wechat-decrypt")
KEY_SCRIPT = Path("scripts/windows/extract_raw_key.py")
DECRYPT_SCRIPT = Path("scripts/windows/decrypt_all.py")
HEX_SECRET = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def _tool_ready(root: Path) -> bool:
    return (root / KEY_SCRIPT).is_file() and (root / DECRYPT_SCRIPT).is_file()


def _tool_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _path_from_ini(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            value = raw.decode(enc).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
        if value and len(value) < 260 and Path(value).is_dir():
            return value
    return ""


def find_data_roots(explicit: str | None = None, *, home: Path | None = None,
                    environ: dict[str, str] | None = None) -> list[Path]:
    home = home or Path.home()
    env = os.environ if environ is None else environ
    bases: list[Path] = []
    if explicit:
        bases.append(Path(explicit).expanduser())
    if env.get("WECHAT_DATA_DIR"):
        bases.append(Path(env["WECHAT_DATA_DIR"]).expanduser())
    if env.get("APPDATA"):
        folder = Path(env["APPDATA"]) / "Tencent" / "xwechat" / "config"
        if folder.is_dir():
            for ini in sorted(folder.glob("*.ini")):
                value = _path_from_ini(ini)
                if value:
                    bases.append(Path(value))
    bases.append(home / "Documents")
    results: list[Path] = []
    visited: set[str] = set()
    for base in bases:
        root = base if base.name.casefold() == "xwechat_files" else base / "xwechat_files"
        if not root.is_dir() or not next(root.glob("*/db_storage/message/message_0.db"), None):
            continue
        normalized = os.path.normcase(str(root.resolve()))
        if normalized not in visited:
            visited.add(normalized)
            results.append(root.resolve())
    return results


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (float, int)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_dump(data: dict[str, Any], indent: int = 0) -> list[str]:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict) and value:
            lines.append(" " * indent + str(key) + ":")
            lines.extend(_yaml_dump(value, indent + 2))
        else:
            lines.append(" " * indent + str(key) + ": " + _encode(value))
    return lines


def save_connection(config_path: Path, *, source: dict[str, Any],
                    refresh: dict[str, Any] | None = None) -> None:
    """Update only source/refresh, preserve unrelated user settings and back up."""
    if config_path.exists():
        existing = load_text_config(config_path.read_text(encoding="utf-8-sig"), config_path.suffix.lower())
    else:
        existing = {}
    data = copy.deepcopy(existing)
    data["source"] = {**(data.get("source") or {}), **source}
    if refresh is not None:
        data["refresh"] = {**(data.get("refresh") or {}), **refresh}
    if config_path.suffix.lower() == ".json":
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        text = "\n".join(_yaml_dump(data)) + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.is_file():
        shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + ".bak"))
    temp = config_path.with_name(config_path.name + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, config_path)
    finally:
        if temp.exists():
            temp.unlink()


def _key_exists(tool_dir: Path, *, newer_than: float = 0) -> bool:
    """Inspect metadata only; never read or display secret contents."""
    path = tool_dir / "key_windows.txt"
    try:
        st = path.stat()
        return st.st_size in (64, 65, 66) and st.st_mtime > newer_than
    except OSError:
        return False


def _ensure_tool(tool_dir: Path) -> None:
    if _tool_ready(tool_dir):
        return
    if tool_dir.exists() and any(tool_dir.iterdir()):
        raise WSError("setup_conflict", "工具目录已有其他文件；请另选 --tool-dir，避免覆盖")
    if not shutil.which("git"):
        raise WSError("setup_failed", "安装外部工具需要 git；先安装 Git 或提供 --tool-dir")
    tool_dir.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["git", "clone", "--depth", "1", TOOL_REPO, str(tool_dir)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240)
    if p.returncode != 0 or not _tool_ready(tool_dir):
        raise WSError("setup_failed", "外部工具下载失败或入口文件不存在",
                      details={"stderr_tail": HEX_SECRET.sub("[redacted]", (p.stderr or "")[-400:])})


def _ensure_venv(tool_dir: Path) -> Path:
    py = _tool_python(tool_dir)
    if not py.is_file():
        p = subprocess.run([sys.executable, "-m", "venv", str(tool_dir / ".venv")],
                           capture_output=True, timeout=120)
        if p.returncode != 0 or not py.is_file():
            raise WSError("setup_failed", "创建外部工具隔离 Python 环境失败")
    p = subprocess.run([str(py), "-c", "import frida, Crypto, zstandard"],
                       capture_output=True, timeout=15)
    if p.returncode != 0:
        p = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check",
                            "frida", "pycryptodome", "zstandard"],
                           capture_output=True, timeout=300)
        if p.returncode != 0:
            raise WSError("setup_failed", "安装外部工具依赖失败（Frida/Crypto/Zstandard）")
    return py


def _extract_key(tool_dir: Path, py: Path, wait_seconds: int) -> bool:
    """Only run after explicit user consent: upstream script terminates Weixin.exe."""
    wait_seconds = min(300, max(30, wait_seconds))
    before = (tool_dir / "key_windows.txt").stat().st_mtime if _key_exists(tool_dir) else 0
    print("提示：取钥工具将结束电脑版微信；请在提示后从桌面重新打开并登录微信。", file=sys.stderr)
    p = subprocess.Popen([str(py), "-u", str(KEY_SCRIPT), str(wait_seconds)],
                         cwd=str(tool_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    def consume() -> None:
        assert p.stdout is not None
        for line in p.stdout:
            print(HEX_SECRET.sub("[redacted]", line.rstrip()), file=sys.stderr)
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    end = time.monotonic() + wait_seconds + 60
    try:
        while time.monotonic() < end:
            if _key_exists(tool_dir, newer_than=before):
                break
            if p.poll() is not None:
                break
            time.sleep(0.5)
        if p.poll() is None:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
    finally:
        reader.join(timeout=2)
    return _key_exists(tool_dir, newer_than=before)


def _need(code: str, detail: str, next_command: str, **more: Any) -> tuple[dict[str, Any], int]:
    return {"ok": False, "status": "needs_user", "code": code, "message": detail,
            "next": next_command, **more}, 2


def run_setup(config: dict[str, Any], args: Any) -> tuple[dict[str, Any], int]:
    cfg_path = resolve_config_path(getattr(args, "config", None))
    tool_dir = Path(getattr(args, "tool_dir", None) or TOOL_DEFAULT).expanduser().resolve()
    source = config.get("source") or {}
    mirror = getattr(args, "use_mirror", None)
    if mirror:
        with open_source(config, source_dir=mirror, account=getattr(args, "account", None)) as src:
            doctor = src.doctor()
        if doctor.get("readable_message_dbs", 0) < 1:
            raise WSError("database_unreadable", "镜像没有可读取的微信消息数据库")
        save_connection(cfg_path, source={"type": "decrypted_dir", "path": str(Path(mirror).expanduser().resolve()),
                                          "account": getattr(args, "account", None) or "", "paths": {}},
                        refresh={"command": [], "cwd": ""})
        return {"ok": True, "status": "ready", "source": "existing_plaintext_mirror",
                "config_path": str(cfg_path), "doctor": doctor,
                "next": "python wxsummary.py groups；python wxsummary.py prepare --chats 群名"}, 0

    roots = find_data_roots(getattr(args, "wechat_data", None))
    configured_path = str(source.get("path") or "")
    configured_ready = False
    if configured_path and Path(configured_path).expanduser().is_dir():
        try:
            with open_source(config) as src:
                src.doctor()
            configured_ready = True
        except (WSError, OSError):
            pass
    result: dict[str, Any] = {
        "ok": configured_ready, "status": "ready" if configured_ready else "needs_user",
        "platform": platform.system(), "config_path": str(cfg_path),
        "configured_mirror": configured_path if configured_ready else None,
        "encrypted_data_roots": [str(p) for p in roots],
        "third_party_tool_installed": _tool_ready(tool_dir),
        "key_file_present": _key_exists(tool_dir),
        "tool_dir": str(tool_dir),
        "warning": "非官方工具可能触发微信账号风控；明文聊天和密钥仅保留本机，云端 Agent 可能收到聊天原文。",
    }
    if getattr(args, "status", False) or not (getattr(args, "install_tool", False) or getattr(args, "extract_key", False)):
        if configured_ready:
            result["next"] = "python wxsummary.py refresh；python wxsummary.py prepare --chats 群名"
            return result, 0
        if not roots:
            return _need("wechat_data_missing", "未找到本机微信 4.x 数据目录，请先登录 Windows 微信。",
                         "python wxsummary.py setup --wechat-data \"你的xwechat_files目录\"",
                         diagnostics=result)
        return _need("installation_required", "检测到本地加密数据，但尚未完成授权接入。",
                     "python wxsummary.py setup --install-tool --trust-third-party-code",
                     diagnostics=result)

    if platform.system() != "Windows":
        raise WSError("setup_unsupported", "自动取钥和解密仅支持 Windows 微信 4.x；其他系统请导入已有明文镜像或 JSON")
    if not roots:
        return _need("wechat_data_missing", "没有检测到微信 4.x 本地数据；请先在电脑微信登录并完成同步。",
                     "python wxsummary.py setup --wechat-data \"你的xwechat_files目录\"")
    default_root = (Path.home() / "Documents" / "xwechat_files").resolve()
    if roots[0] != default_root:
        return _need("custom_storage", "外部工具目前只支持默认文档目录；检测到微信自定义存储路径。",
                     "请先通过可靠的手工导出方式提供明文镜像；本程序不会自动建立目录联接或移动数据。",
                     detected_root=str(roots[0]))
    accounts = list(roots[0].glob("*/db_storage/message/message_0.db"))
    if len(accounts) != 1:
        return _need("multiple_accounts", "检测到多个微信数据账号；外部工具默认选最新目录，不能确保符合指定账号。",
                     "请先明确所需账号并使用单账号授权镜像；本程序不会擅自选取其他账号。",
                     account_count=len(accounts))
    if getattr(args, "install_tool", False):
        if not getattr(args, "trust_tool", False):
            return _need("third_party_consent", "下载并执行第三方开源代码需明确授权。",
                         "python wxsummary.py setup --install-tool --trust-third-party-code")
        _ensure_tool(tool_dir)
        _ensure_venv(tool_dir)
    if not _tool_ready(tool_dir) or not _tool_python(tool_dir).is_file():
        return _need("installation_required", "解密工具尚未安装或依赖未就绪。",
                     "python wxsummary.py setup --install-tool --trust-third-party-code")
    if getattr(args, "extract_key", False):
        if not getattr(args, "consent_restart", False):
            return _need("restart_consent", "取钥会强制关闭微信进程，然后需要你从桌面重新启动微信。",
                         "python wxsummary.py setup --extract-key --consent-restart-wechat")
        if not _extract_key(tool_dir, _tool_python(tool_dir), getattr(args, "key_wait", 120)):
            return _need("key_failed", "没有获取到经过验证的密钥；微信版本或权限可能不兼容。",
                         "检查外部工具兼容性，勿重复尝试未知来源取钥脚本")
    if not _key_exists(tool_dir):
        return _need("key_required", "需要首次授权取钥才能读取本机加密聊天库。",
                     "python wxsummary.py setup --extract-key --consent-restart-wechat")
    conf = {"refresh": {"cwd": str(tool_dir),
                        "command": [str(_tool_python(tool_dir)), DECRYPT_SCRIPT.as_posix()],
                        "timeout_seconds": 1800}}
    refreshed = run_refresh(conf)
    decrypted = tool_dir / "decrypted"
    account = accounts[0].parents[2].name
    with open_source({**config, "source": {"type": "decrypted_dir", "path": str(decrypted), "account": account}}) as src:
        doctor = src.doctor()
    save_connection(cfg_path, source={"type": "decrypted_dir", "path": str(decrypted),
                                      "account": account, "paths": {}},
                    refresh=conf["refresh"])
    return {"ok": True, "status": "ready", "source": "local_wechat_4x",
            "config_path": str(cfg_path), "tool_dir": str(tool_dir),
            "refreshed": {"elapsed_seconds": refreshed["elapsed_seconds"]}, "doctor": doctor,
            "next": "python wxsummary.py groups；python wxsummary.py prepare --refresh --chats 群名"}, 0

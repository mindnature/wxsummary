"""主题注册表：扫描仓库根目录 themes/<name>/（theme.json + template.html + style.css）。

新增主题只需新建目录，不改代码。themes/_shared 放公共片段，不作为主题列出。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import REPO_ROOT
from .errors import WSError

THEMES_DIR = REPO_ROOT / "themes"
SHARED_DIR = THEMES_DIR / "_shared"

DEFAULT_PALETTE = {
    "bar": "#c96442",
    "bar_alt": "#e0b49a",
    "axis": "#8a8578",
    "text": "#3d3929",
    "grid": "#e8e4d8",
    "bg": "transparent",
}


def _read_meta(folder: Path) -> dict[str, Any]:
    meta_path = folder / "theme.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise WSError("render_failed", f"{meta_path} 不是合法 JSON：{exc}") from exc
    meta.setdefault("name", folder.name)
    meta.setdefault("label", folder.name)
    meta.setdefault("description", "")
    meta["palette"] = {**DEFAULT_PALETTE, **(meta.get("palette") or {})}
    meta["dir"] = str(folder)
    return meta


def list_themes() -> list[dict[str, Any]]:
    if not THEMES_DIR.is_dir():
        return []
    themes = []
    for folder in sorted(THEMES_DIR.iterdir()):
        if folder.is_dir() and not folder.name.startswith(("_", ".")) and (folder / "template.html").is_file():
            meta = _read_meta(folder)
            themes.append({k: meta[k] for k in ("name", "label", "description", "order") if k in meta})
    return sorted(themes, key=lambda t: (t.get("order", 99), t["name"]))


def get_theme(name: str) -> dict[str, Any]:
    folder = THEMES_DIR / name
    if not (folder / "template.html").is_file():
        raise WSError("theme_not_found", f"主题不存在：{name}", details=[t["name"] for t in list_themes()])
    return _read_meta(folder)


def resolve_theme_names(requested: list[str], default: list[str]) -> list[str]:
    names = requested or default or ["newspaper"]
    available = [t["name"] for t in list_themes()]
    if any(n.lower() == "all" for n in names):
        return available
    result = []
    for n in names:
        if n not in available:
            raise WSError("theme_not_found", f"主题不存在：{n}", details=available)
        if n not in result:
            result.append(n)
    return result

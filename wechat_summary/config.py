"""配置加载。

配置文件默认在 ~/.wxsummary/config.yaml，可用 --config 或环境变量 WS_CONFIG 覆盖。
支持 .yaml/.yml（内置 YAML 子集解析器，安装了 PyYAML 时优先用它）与 .json。
敏感字段可用环境变量覆盖，日志与 --json 输出一律脱敏。
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from .errors import WSError

DEFAULT_CONFIG_PATH = Path("~/.wxsummary/config.yaml")

DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "week_start": "monday",
    "source": {
        "type": "decrypted_dir",
        "path": "",
        "account": "",
        "paths": {},
    },
    "refresh": {"command": "", "cwd": "", "timeout_seconds": 1800},
    "me": {"ids": [], "names": []},
    "group_sets": {},
    "output": {
        "root": "~/Documents/wxsummary",
        "chunk_messages": 1500,
        "text_limit": 600,
    },
"render": {"themes": ["newspaper"], "formats": ["md", "html", "png"]},
}

ENV_OVERRIDES = {
    "WS_SOURCE_DIR": ("source", "path"),
}

SECRET_KEY_RE = re.compile(r"(secret|token|password|passwd|api_?key|^key$|raw_key)", re.I)


# ---------------------------------------------------------------- YAML 子集解析
class _YamlError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline(text: str) -> list[str]:
    parts, depth, quote, buf = [], 0, None, []
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text[0] == text[-1] and text[0] in "\"'" and len(text) >= 2:
        body = text[1:-1]
        if text[0] == '"':
            try:
                return json.loads(text)
            except ValueError:
                return body
        return body.replace("''", "'")
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(p) for p in _split_inline(text[1:-1])]
    if text.startswith("{") and text.endswith("}"):
        result = {}
        for part in _split_inline(text[1:-1]):
            key, _, value = part.partition(":")
            result[_scalar(key)] = _scalar(value)
        return result
    lower = text.lower()
    if lower in ("true", "yes", "on"):
        return True
    if lower in ("false", "no", "off"):
        return False
    if lower in ("null", "~", "none"):
        return None
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?\d*\.\d+", text):
        return float(text)
    return text


def _split_key(content: str) -> tuple[str, str] | None:
    quote = None
    for i, ch in enumerate(content):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1:].strip()
    return None


def parse_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise _YamlError("缩进不能使用 Tab")
        stripped = _strip_comment(raw)
        if stripped.strip() in ("", "---"):
            continue
        lines.append((len(stripped) - len(stripped.lstrip(" ")), stripped.strip()))

    def block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return None, index
        if lines[index][1].startswith("- ") or lines[index][1] == "-":
            return seq(index, indent)
        return mapping(index, indent)

    def seq(index: int, indent: int) -> tuple[list, int]:
        items: list[Any] = []
        while index < len(lines):
            ind, content = lines[index]
            if ind < indent or not (content.startswith("- ") or content == "-"):
                break
            if ind > indent:
                raise _YamlError(f"列表缩进不一致：{content}")
            rest = content[1:].strip()
            index += 1
            if not rest:
                value, index = block(index, lines[index][0]) if index < len(lines) and lines[index][0] > ind else (None, index)
                items.append(value)
                continue
            kv = _split_key(rest) if not rest.startswith(("[", "{", '"', "'")) else None
            if kv is None:
                items.append(_scalar(rest))
                continue
            # "- key: value" 形式的映射项
            child_indent = ind + 2
            lines.insert(index, (child_indent, rest))
            value, index = mapping(index, child_indent)
            items.append(value)
        return items, index

    def mapping(index: int, indent: int) -> tuple[dict, int]:
        result: dict[str, Any] = {}
        while index < len(lines):
            ind, content = lines[index]
            if ind < indent:
                break
            if ind > indent:
                raise _YamlError(f"缩进不一致：{content}")
            if content.startswith("- "):
                break
            kv = _split_key(content)
            if kv is None:
                raise _YamlError(f"无法解析：{content}")
            key, rest = kv
            key = _scalar(key) if key[:1] in "\"'" else key
            index += 1
            if rest in ("", "|", ">"):
                if index < len(lines) and (lines[index][0] > ind or (lines[index][0] == ind and lines[index][1].startswith("- "))):
                    value, index = block(index, lines[index][0])
                else:
                    value = None
                result[str(key)] = value
            else:
                result[str(key)] = _scalar(rest)
        return result, index

    if not lines:
        return {}
    value, index = block(0, lines[0][0])
    if index != len(lines):
        raise _YamlError(f"无法解析：{lines[index][1]}")
    return value


def load_text_config(text: str, suffix: str) -> dict[str, Any]:
    if suffix == ".json":
        data = json.loads(text)
    else:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text)
        except ImportError:
            data = parse_yaml(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise WSError("config_invalid", "配置文件顶层必须是映射（key: value）")
    return data


# ---------------------------------------------------------------- 合并与访问
def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and key != "group_sets":
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("WS_CONFIG"):
        return Path(os.environ["WS_CONFIG"]).expanduser()
    return DEFAULT_CONFIG_PATH.expanduser()


def load_config(explicit: str | None = None, *, required: bool = False) -> dict[str, Any]:
    path = resolve_config_path(explicit)
    user: dict[str, Any] = {}
    if path.is_file():
        try:
            user = load_text_config(path.read_text(encoding="utf-8-sig"), path.suffix.lower())
        except (ValueError, _YamlError) as exc:
            raise WSError("config_invalid", f"配置文件解析失败：{path}：{exc}") from exc
    elif explicit or required:
        raise WSError("config_invalid", f"配置文件不存在：{path}")
    config = deep_merge(DEFAULTS, user)
    for env, (section, key) in ENV_OVERRIDES.items():
        if os.environ.get(env):
            config.setdefault(section, {})[key] = os.environ[env]
    config["_path"] = str(path) if path.is_file() else None
    _normalize(config)
    return config


def _normalize(config: dict[str, Any]) -> None:
    me = config.get("me") or {}
    for key in ("ids", "names"):
        value = me.get(key)
        if value is None:
            me[key] = []
        elif isinstance(value, str):
            me[key] = [value]
    config["me"] = me
    render = config.get("render") or {}
    for key in ("themes", "formats"):
        value = render.get(key)
        if isinstance(value, str):
            render[key] = [v.strip() for v in value.split(",") if v.strip()]
    sets = config.get("group_sets") or {}
    if not isinstance(sets, dict):
        raise WSError("config_invalid", "group_sets 必须是 {集合名: [群名或群ID, ...]}")
    for name, members in list(sets.items()):
        if isinstance(members, str):
            sets[name] = [members]
        elif not isinstance(members, list):
            raise WSError("config_invalid", f"group_sets.{name} 必须是列表")
    config["group_sets"] = sets
    if str(config.get("week_start", "monday")).lower() not in WEEKDAYS:
        raise WSError("config_invalid", "week_start 只能是 monday..sunday")


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def redact(value: Any, key: str = "") -> Any:
    """递归脱敏：键名像密钥的值替换为 ***。"""
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if key and SECRET_KEY_RE.search(key) and value not in (None, "", [], {}):
        return "***"
    return value

"""极简模板引擎（标准库实现，默认 HTML 转义）。

语法：
  {{ expr }}            输出并转义；expr 为点号路径，可接过滤器：{{ x.y | raw }} {{ n | pct }}
  {% if expr %}...{% elif expr %}...{% else %}...{% endif %}   支持 not / and / or / == / != / > / <
  {% for item in expr %}...{% else %}...{% endfor %}            循环内可用 loop.index / loop.first / loop.last
  {% include "partial.html" %}  或  {% include "partial.html" with item %}（把 item 的字段放进作用域）
  {# 注释 #}
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Callable

TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.S)


class TemplateError(Exception):
    pass


def _f_pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _f_hm(v: Any) -> str:
    return str(v or "")[11:16]


def _f_md(v: Any) -> str:
    s = str(v or "")
    return f"{int(s[5:7])}月{int(s[8:10])}日" if len(s) >= 10 and s[4] == "-" else s


def _f_date(v: Any) -> str:
    return str(v or "")[:10]


def _f_num(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v or "")


def _f_pad2(v: Any) -> str:
    try:
        return f"{int(v):02d}"
    except (TypeError, ValueError):
        return str(v or "")


def _f_first(v: Any) -> str:
    s = str(v or "")
    return s[:1]


def _f_rest(v: Any) -> str:
    s = str(v or "")
    return s[1:]


def _f_join(v: Any) -> str:
    return "、".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v or "")


def _f_upper(v: Any) -> str:
    return str(v or "").upper()


def _f_length(v: Any) -> int:
    try:
        return len(v)
    except TypeError:
        return 0


def _f_trunc60(v: Any) -> str:
    s = str(v or "")
    return s if len(s) <= 60 else s[:58] + "…"


def _f_plus1(v: Any) -> Any:
    try:
        return int(v) + 1
    except (TypeError, ValueError):
        return v


FILTERS: dict[str, Callable[[Any], Any]] = {
    "pct": _f_pct, "hm": _f_hm, "md": _f_md, "date": _f_date, "num": _f_num, "pad2": _f_pad2,
    "first": _f_first, "rest": _f_rest, "join": _f_join, "upper": _f_upper, "length": _f_length,
    "trunc60": _f_trunc60, "plus1": _f_plus1,
}


class _Raw(str):
    pass


def _lookup(path: str, ctx: list[dict[str, Any]]) -> Any:
    path = path.strip()
    if not path:
        return None
    if path[0] in "\"'" and path[-1] == path[0]:
        return path[1:-1]
    if re.fullmatch(r"-?\d+", path):
        return int(path)
    if path in ("true", "True"):
        return True
    if path in ("false", "False", "none", "None"):
        return False if path.lower() == "false" else None
    parts = path.split(".")
    value: Any = None
    for scope in reversed(ctx):
        if parts[0] in scope:
            value = scope[parts[0]]
            break
    else:
        return None
    for part in parts[1:]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)) and part.isdigit():
            idx = int(part)
            value = value[idx] if idx < len(value) else None
        else:
            value = getattr(value, part, None)
        if value is None:
            return None
    return value


def _eval_value(expr: str, ctx: list[dict[str, Any]]) -> Any:
    pieces = [p.strip() for p in expr.split("|")]
    value = _lookup(pieces[0], ctx)
    for f in pieces[1:]:
        if f == "raw":
            value = _Raw("" if value is None else str(value))
            continue
        if f not in FILTERS:
            raise TemplateError(f"未知过滤器：{f}")
        value = FILTERS[f](value)
    return value


def _eval_cond(expr: str, ctx: list[dict[str, Any]]) -> bool:
    expr = expr.strip()
    if " or " in expr:
        return any(_eval_cond(p, ctx) for p in expr.split(" or "))
    if " and " in expr:
        return all(_eval_cond(p, ctx) for p in expr.split(" and "))
    if expr.startswith("not "):
        return not _eval_cond(expr[4:], ctx)
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if f" {op} " in expr:
            left, right = (s.strip() for s in expr.split(f" {op} ", 1))
            a, b = _eval_value(left, ctx), _eval_value(right, ctx)
            try:
                return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]
            except TypeError:
                return False
    return bool(_eval_value(expr, ctx))


class Template:
    def __init__(self, source: str, loader: "Loader", name: str = "<template>"):
        self.name = name
        self.loader = loader
        self.tokens = [t for t in TOKEN_RE.split(source) if t != ""]
        self.pos = 0
        self.tree = self._parse(())

    def _parse(self, until: tuple[str, ...]) -> list[Any]:
        nodes: list[Any] = []
        while self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            if tok.startswith("{#"):
                self.pos += 1
                continue
            if tok.startswith("{{"):
                nodes.append(("var", tok[2:-2].strip()))
                self.pos += 1
                continue
            if tok.startswith("{%"):
                stmt = tok[2:-2].strip()
                head = stmt.split(None, 1)[0]
                if head in until:
                    return nodes
                self.pos += 1
                if head == "if":
                    branches = [(stmt[3:].strip(), self._parse(("elif", "else", "endif")))]
                    else_body: list[Any] = []
                    while True:
                        cur = self.tokens[self.pos][2:-2].strip()
                        self.pos += 1
                        if cur.startswith("elif"):
                            branches.append((cur[5:].strip(), self._parse(("elif", "else", "endif"))))
                        elif cur == "else":
                            else_body = self._parse(("endif",))
                        elif cur == "endif":
                            break
                        else:
                            raise TemplateError(f"{self.name}: 意外的 {cur}")
                    nodes.append(("if", branches, else_body))
                elif head == "for":
                    m = re.fullmatch(r"for\s+(\w+)\s+in\s+(.+)", stmt)
                    if not m:
                        raise TemplateError(f"{self.name}: for 语法错误：{stmt}")
                    body = self._parse(("else", "endfor"))
                    empty: list[Any] = []
                    cur = self.tokens[self.pos][2:-2].strip()
                    self.pos += 1
                    if cur == "else":
                        empty = self._parse(("endfor",))
                        self.pos += 1
                    nodes.append(("for", m.group(1), m.group(2).strip(), body, empty))
                elif head == "include":
                    rest = stmt.split(None, 1)[1].strip()
                    m = re.fullmatch(r"""["']([^"']+)["'](?:\s+with\s+(\S+))?""", rest)
                    if not m:
                        raise TemplateError(f"{self.name}: include 语法错误：{stmt}")
                    nodes.append(("include", m.group(1), m.group(2)))
                else:
                    raise TemplateError(f"{self.name}: 未知语句 {head}")
                continue
            nodes.append(("text", tok))
            self.pos += 1
        if until:
            raise TemplateError(f"{self.name}: 缺少 {'/'.join(until)}")
        return nodes

    def render(self, context: dict[str, Any]) -> str:
        out: list[str] = []
        self._render(self.tree, [context], out)
        return "".join(out)

    def _render(self, nodes: list[Any], ctx: list[dict[str, Any]], out: list[str]) -> None:
        for node in nodes:
            kind = node[0]
            if kind == "text":
                out.append(node[1])
            elif kind == "var":
                value = _eval_value(node[1], ctx)
                if value is None or value is False:
                    continue
                out.append(value if isinstance(value, _Raw) else html.escape(str(value)))
            elif kind == "if":
                for cond, body in node[1]:
                    if _eval_cond(cond, ctx):
                        self._render(body, ctx, out)
                        break
                else:
                    self._render(node[2], ctx, out)
            elif kind == "for":
                _, var, expr, body, empty = node
                items = _eval_value(expr, ctx) or []
                if isinstance(items, dict):
                    items = list(items.items())
                items = list(items)
                if not items:
                    self._render(empty, ctx, out)
                for i, item in enumerate(items):
                    loop = {"index": i + 1, "index0": i, "first": i == 0, "last": i == len(items) - 1, "length": len(items)}
                    self._render(body, ctx + [{var: item, "loop": loop}], out)
            elif kind == "include":
                tpl = self.loader.load(node[1])
                scope = ctx
                if node[2]:
                    obj = _lookup(node[2], ctx)
                    if isinstance(obj, dict):
                        scope = ctx + [obj]
                tpl._render(tpl.tree, scope, out)


class Loader:
    def __init__(self, dirs: list[Path]):
        self.dirs = dirs
        self.cache: dict[str, Template] = {}

    def load(self, name: str) -> Template:
        if name not in self.cache:
            for d in self.dirs:
                path = d / name
                if path.is_file():
                    self.cache[name] = Template(path.read_text(encoding="utf-8"), self, name)
                    break
            else:
                raise TemplateError(f"找不到模板 {name}（查找：{', '.join(str(d) for d in self.dirs)}）")
        return self.cache[name]

"""内联 SVG 图表：不依赖任何外部库或网络资源。

style 参数：
- "clean"：常规圆角柱
- "sketch"：手绘抖动（doodle 主题）
- "ascii"：返回等宽字符条（terminal 主题直接用文本，不出 SVG）
"""
from __future__ import annotations

import html
import math
import random
from typing import Any


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _jitter_rect(x: float, y: float, w: float, h: float, rnd: random.Random, amp: float = 1.4) -> str:
    """手绘感矩形：四条边各自轻微抖动。"""
    def j():
        return rnd.uniform(-amp, amp)

    pts = [(x + j(), y + j()), (x + w + j(), y + j()), (x + w + j(), y + h + j()), (x + j(), y + h + j())]
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f} " + " ".join(f"L{px:.1f},{py:.1f}" for px, py in pts[1:]) + " Z"
    return d


def bar_chart(values: list[float], labels: list[str], palette: dict[str, str], *, width: int = 640, height: int = 180,
              style: str = "clean", highlight: int | None = None, title: str = "", label_every: int = 1,
              value_labels: bool = False) -> str:
    n = len(values)
    if n == 0:
        return ""
    pad_l, pad_r, pad_t, pad_b = 8, 8, 18 if value_labels else 8, 26
    chart_w, chart_h = width - pad_l - pad_r, height - pad_t - pad_b
    peak = max(values) or 1
    slot = chart_w / n
    bar_w = max(2.0, slot * (0.62 if n > 12 else 0.56))
    rnd = random.Random(42)
    parts = [f'<svg class="ws-chart" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{_esc(title)}" xmlns="http://www.w3.org/2000/svg">']
    if title:
        parts.append(f"<title>{_esc(title)}</title>")
    base_y = pad_t + chart_h
    parts.append(f'<line x1="{pad_l}" y1="{base_y:.1f}" x2="{width - pad_r}" y2="{base_y:.1f}" stroke="{palette["axis"]}" stroke-width="1"/>')
    for i, v in enumerate(values):
        h = 0 if v <= 0 else max(2.0, chart_h * v / peak)
        x = pad_l + slot * i + (slot - bar_w) / 2
        y = base_y - h
        color = palette["bar"] if (highlight is None or i == highlight) else palette["bar_alt"]
        if h > 0:
            if style == "sketch":
                parts.append(f'<path d="{_jitter_rect(x, y, bar_w, h, rnd)}" fill="{color}" fill-opacity="0.85" stroke="{palette["text"]}" stroke-width="1.3" stroke-linejoin="round"/>')
            else:
                r = min(3.0, bar_w / 3)
                parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" rx="{r:.1f}" fill="{color}"/>')
            if value_labels:
                parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" font-size="10" fill="{palette["text"]}">{_esc(int(v))}</text>')
        if labels and (i % label_every == 0 or i == n - 1):
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{base_y + 16:.1f}" text-anchor="middle" font-size="10" fill="{palette["axis"]}">{_esc(labels[i])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def hbar_chart(rows: list[tuple[str, float]], palette: dict[str, str], *, width: int = 640, row_h: int = 26,
               style: str = "clean", title: str = "") -> str:
    if not rows:
        return ""
    label_w = 120
    height = row_h * len(rows) + 6
    peak = max(v for _, v in rows) or 1
    rnd = random.Random(7)
    parts = [f'<svg class="ws-chart" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{_esc(title)}" xmlns="http://www.w3.org/2000/svg">']
    for i, (name, v) in enumerate(rows):
        y = i * row_h + 4
        bar_max = width - label_w - 48
        w = max(2.0, bar_max * v / peak)
        label = name if len(name) <= 9 else name[:8] + "…"
        parts.append(f'<text x="{label_w - 8}" y="{y + row_h * 0.62:.1f}" text-anchor="end" font-size="12" fill="{palette["text"]}">{_esc(label)}</text>')
        color = palette["bar"] if i == 0 else palette["bar_alt"]
        if style == "sketch":
            parts.append(f'<path d="{_jitter_rect(label_w, y + 4, w, row_h - 10, rnd, 1.2)}" fill="{color}" fill-opacity="0.85" stroke="{palette["text"]}" stroke-width="1.2"/>')
        else:
            parts.append(f'<rect x="{label_w}" y="{y + 4}" width="{w:.1f}" height="{row_h - 10}" rx="3" fill="{color}"/>')
        parts.append(f'<text x="{label_w + w + 6:.1f}" y="{y + row_h * 0.62:.1f}" font-size="11" fill="{palette["axis"]}">{_esc(int(v))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def ascii_bars(rows: list[tuple[str, float]], width: int = 28, char: str = "█", rest: str = "░") -> list[dict[str, str]]:
    peak = max((v for _, v in rows), default=0) or 1
    out = []
    for name, v in rows:
        filled = int(round(width * v / peak))
        out.append({"label": name, "bar": char * filled + rest * (width - filled), "value": str(int(v))})
    return out


def sparkline(values: list[float], palette: dict[str, str], *, width: int = 220, height: int = 44) -> str:
    if len(values) < 2:
        return ""
    peak = max(values) or 1
    step = width / (len(values) - 1)
    pts = [(i * step, height - 4 - (height - 8) * v / peak) for i, v in enumerate(values)]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = d + f" L{width:.1f},{height} L0,{height} Z"
    return (f'<svg class="ws-spark" viewBox="0 0 {width} {height}" width="100%" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="{area}" fill="{palette["bar"]}" fill-opacity="0.15"/>'
            f'<path d="{d}" fill="none" stroke="{palette["bar"]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def donut(parts_in: list[tuple[str, float]], palette_list: list[str], *, size: int = 120) -> str:
    total = sum(v for _, v in parts_in) or 1
    r, cx, cy, stroke = size / 2 - 10, size / 2, size / 2, 16
    circ = 2 * math.pi * r
    offset = 0.0
    segs = []
    for i, (_name, v) in enumerate(parts_in):
        frac = v / total
        color = palette_list[i % len(palette_list)]
        segs.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{color}" stroke-width="{stroke}" '
                    f'stroke-dasharray="{circ * frac:.2f} {circ:.2f}" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        offset += circ * frac
    return f'<svg class="ws-donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">{"".join(segs)}</svg>'


def build_charts(report: dict[str, Any], palette: dict[str, str], style: str) -> dict[str, str]:
    stats = report["stats"]
    hourly = stats["hourly"]
    peak_hour = stats.get("peak_hour")
    charts = {
        "hourly": bar_chart(hourly, [f"{h}" for h in range(24)], palette, style=style, highlight=peak_hour,
                            title="24 小时发言分布", label_every=3),
        "senders": hbar_chart([(s["name"], s["messages"]) for s in stats["top_senders"][:8]], palette, style=style, title="发言排行"),
    }
    daily = stats.get("daily") or []
    if len(daily) > 1:
        values = [d["messages"] for d in daily]
        peak_idx = max(range(len(values)), key=lambda i: values[i]) if any(values) else None
        every = 1 if len(daily) <= 8 else (2 if len(daily) <= 16 else 5)
        charts["daily"] = bar_chart(values, [d["date"][5:].replace("-", "/") for d in daily], palette, style=style,
                                    highlight=peak_idx, title="每日发言趋势", label_every=every, value_labels=len(daily) <= 10)
        charts["daily_spark"] = sparkline(values, palette)
    else:
        charts["daily"] = ""
        charts["daily_spark"] = ""
    types = [(t["label"], t["count"]) for t in stats["types"][:6]]
    charts["types_donut"] = donut(types, [palette["bar"], palette["bar_alt"], palette["axis"], palette["text"], palette["grid"], "#999999"])
    return charts

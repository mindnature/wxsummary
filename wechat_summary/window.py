"""报告时间窗：一律左闭右开 [since, until)，按配置时区计算。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from .config import WEEKDAYS
from .errors import WSError

PERIOD_LABELS = {"daily": "日报", "weekly": "周报", "monthly": "月报", "custom": "专题报告"}


@dataclass(frozen=True)
class Window:
    period: str
    since: datetime
    until: datetime
    tz_name: str

    @property
    def label(self) -> str:
        return PERIOD_LABELS[self.period]

    @property
    def days(self) -> list[date]:
        result, day = [], self.since.date()
        last = (self.until - timedelta(microseconds=1)).date()
        while day <= last:
            result.append(day)
            day += timedelta(days=1)
        return result

    def range_text(self) -> str:
        """给人看的范围描述（until 是开区间，展示时回退到前一天/前一刻）。"""
        start, end = self.since, self.until
        whole_days = start.time() == time(0) and end.time() == time(0)
        if whole_days:
            last = (end - timedelta(days=1)).date()
            if last == start.date():
                return start.strftime("%Y-%m-%d")
            return f"{start:%Y-%m-%d} 至 {last:%Y-%m-%d}"
        return f"{start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}"

    def slug(self) -> str:
        start, end = self.since, self.until
        if self.period == "daily":
            return f"{start:%Y-%m-%d}"
        if self.period == "weekly":
            iso = start.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        if self.period == "monthly":
            return f"{start:%Y-%m}"
        if start.time() == time(0) and end.time() == time(0):
            return f"{start:%Y%m%d}-{(end - timedelta(days=1)):%Y%m%d}"
        return f"{start:%Y%m%dT%H%M}-{end:%Y%m%dT%H%M}"

    def to_dict(self) -> dict[str, str]:
        return {
            "period": self.period,
            "label": self.label,
            "since": self.since.isoformat(timespec="seconds"),
            "until": self.until.isoformat(timespec="seconds"),
            "timezone": self.tz_name,
            "range_text": self.range_text(),
            "slug": self.slug(),
        }


def parse_point(text: str, tz: tzinfo) -> datetime:
    value = str(text).strip()
    if not value:
        raise WSError("window_invalid", "时间不能为空")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.combine(date.fromisoformat(value), time(0), tzinfo=tz)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError as exc:
        raise WSError("window_invalid", f"无法解析时间：{value}") from exc
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _midnight(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=tz)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def compute_window(
    period: str,
    *,
    tz: tzinfo,
    tz_name: str,
    week_start: str = "monday",
    date_text: str | None = None,
    since: str | None = None,
    until: str | None = None,
    now: datetime | None = None,
) -> Window:
    if period not in PERIOD_LABELS:
        raise WSError("window_invalid", f"未知周期：{period}")
    now = (now or datetime.now(tz)).astimezone(tz)
    ws = WEEKDAYS.index(str(week_start).lower())

    if since or until:
        if not (since and until):
            raise WSError("window_invalid", "--since 与 --until 需要同时提供")
        if date_text:
            raise WSError("window_invalid", "--date 不能与 --since/--until 同时使用")
        start, end = parse_point(since, tz), parse_point(until, tz)
        if end <= start:
            raise WSError("window_invalid", "--until 必须晚于 --since")
        return Window("custom", start, end, tz_name)

    if period == "custom":
        raise WSError("window_invalid", "custom 周期需要 --since 与 --until")

    anchor: date
    explicit = date_text is not None
    if explicit:
        anchor = parse_point(date_text, tz).date()
    else:
        anchor = now.date()

    if period == "daily":
        day = anchor if explicit else anchor - timedelta(days=1)
        return Window("daily", _midnight(day, tz), _midnight(day + timedelta(days=1), tz), tz_name)

    if period == "weekly":
        offset = (anchor.weekday() - ws) % 7
        start_day = anchor - timedelta(days=offset)
        if not explicit:
            start_day -= timedelta(days=7)
        return Window("weekly", _midnight(start_day, tz), _midnight(start_day + timedelta(days=7), tz), tz_name)

    # monthly
    first = _month_start(anchor)
    if not explicit:
        first = _month_start(first - timedelta(days=1))
    return Window("monthly", _midnight(first, tz), _midnight(_next_month(first), tz), tz_name)

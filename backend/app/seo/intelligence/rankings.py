"""Ranking maths over Search Console ``date x page`` rows (pure functions).

Positions are impression-weighted averages, as Search Console reports them.
Nothing here forecasts; it only compares measured windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

MIN_IMPRESSIONS_FOR_MOVE = 50


@dataclass(frozen=True)
class DayRow:
    day: date
    path: str
    clicks: float
    impressions: float
    position: float


@dataclass
class Stats:
    clicks: float = 0.0
    impressions: float = 0.0
    weighted_position: float = 0.0

    def add(self, row: DayRow) -> None:
        self.clicks += row.clicks
        self.impressions += row.impressions
        self.weighted_position += row.position * row.impressions

    @property
    def position(self) -> float | None:
        return self.weighted_position / self.impressions if self.impressions else None

    @property
    def ctr(self) -> float | None:
        return self.clicks / self.impressions if self.impressions else None


def page_path(url: str) -> str:
    path = urlparse(url).path or "/"
    return path.rstrip("/") or "/"


def to_rows(raw: list[dict[str, object]]) -> list[DayRow]:
    """Convert GSC rows with ``keys = [date, page]``."""
    rows: list[DayRow] = []
    for item in raw:
        keys = item.get("keys")
        if not isinstance(keys, list) or len(keys) != 2:
            continue
        try:
            rows.append(
                DayRow(
                    day=date.fromisoformat(str(keys[0])),
                    path=page_path(str(keys[1])),
                    clicks=float(item.get("clicks", 0) or 0),
                    impressions=float(item.get("impressions", 0) or 0),
                    position=float(item.get("position", 0) or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return rows


def window(rows: list[DayRow], start: date, end: date) -> dict[str, Stats]:
    out: dict[str, Stats] = {}
    for row in rows:
        if start <= row.day <= end:
            out.setdefault(row.path, Stats()).add(row)
    return out


def total(stats: dict[str, Stats], prefix: str = "/blog/") -> Stats:
    agg = Stats()
    for path, s in stats.items():
        if path.startswith(prefix):
            agg.clicks += s.clicks
            agg.impressions += s.impressions
            agg.weighted_position += s.weighted_position
    return agg


def fmt_pos(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "–"


def fmt_delta(
    new: float | None, old: float | None, *, lower_is_better: bool = False
) -> str:
    if new is None or old is None:
        return "new" if new is not None else "–"
    delta = new - old
    if abs(delta) < 0.05:
        return "±0"
    arrow = (
        ("▲" if delta < 0 else "▼") if lower_is_better else ("▲" if delta > 0 else "▼")
    )
    return (
        f"{arrow} {abs(delta):.1f}" if lower_is_better else f"{arrow} {abs(delta):.0f}"
    )


def verdict(before: Stats | None, after: Stats | None) -> str:
    """'improved', 'declined', 'no clear change', 'newly ranking' or 'not enough data'."""
    if after and after.impressions >= 100 and (not before or before.impressions < 20):
        return "newly ranking"
    if not before or not after or before.impressions < 20 or after.impressions < 20:
        return "not enough data"
    b, a = before.position, after.position
    assert b is not None and a is not None
    clicks_ratio = (after.clicks + 1) / (before.clicks + 1)
    if a <= b - 1 or (clicks_ratio >= 1.2 and after.clicks >= 10 and a <= b + 0.5):
        return "improved"
    if a >= b + 1 or (clicks_ratio <= 0.8 and before.clicks >= 10 and a >= b - 0.5):
        return "declined"
    return "no clear change"


def relative_verdict(
    before: Stats | None,
    after: Stats | None,
    site_before: Stats,
    site_after: Stats,
) -> str:
    """Like ``verdict`` but net of the site-wide trend over the same windows.

    A page that moved exactly as much as all blog pages did gets "in line with
    site", so site-wide growth (or a Google update) is not credited to the change.
    """
    raw = verdict(before, after)
    if raw in {"newly ranking", "not enough data"}:
        return raw
    assert before and after and before.position and after.position
    if not site_before.position or not site_after.position:
        return raw
    page_shift = after.position - before.position
    site_shift = site_after.position - site_before.position
    site_ratio = (site_after.clicks + 1) / (site_before.clicks + 1)
    page_ratio = (after.clicks + 1) / (before.clicks + 1)
    relative_ratio = page_ratio / site_ratio
    relative_shift = page_shift - site_shift
    if relative_shift <= -1 or (
        relative_ratio >= 1.2 and after.clicks >= 10 and relative_shift <= 0.5
    ):
        return "beat site trend"
    if relative_shift >= 1 or (
        relative_ratio <= 0.8 and before.clicks >= 10 and relative_shift >= -0.5
    ):
        return "behind site trend"
    return "in line with site"


def describe(before: Stats | None, after: Stats | None) -> str:
    b = before or Stats()
    a = after or Stats()
    return (
        f"clicks {b.clicks:.0f} -> {a.clicks:.0f}, impressions {b.impressions:.0f} -> "
        f"{a.impressions:.0f}, position {fmt_pos(b.position)} -> {fmt_pos(a.position)}"
    )


@dataclass
class Periods:
    current: tuple[date, date]
    previous: tuple[date, date]


def periods(data_end: date, days: int = 14) -> Periods:
    cur = (data_end - timedelta(days=days - 1), data_end)
    prev = (cur[0] - timedelta(days=days), cur[0] - timedelta(days=1))
    return Periods(cur, prev)


def ranking_table(
    rows: list[DayRow], p: Periods, limit: int = 30
) -> tuple[str, list[dict]]:
    """Markdown table of blog pages plus movers (dicts) for findings."""
    cur, prev = window(rows, *p.current), window(rows, *p.previous)
    blog = [path for path in cur if path.startswith("/blog/")]
    blog.sort(key=lambda path: -cur[path].impressions)
    t_cur, t_prev = total(cur), total(prev)
    lines = [
        f"Search Console, final data. Current {p.current[0]}..{p.current[1]} vs previous "
        f"{p.previous[0]}..{p.previous[1]}. Position: lower is better (▲ = improved).",
        "",
        f"**All blog pages:** clicks {t_cur.clicks:.0f} "
        f"({fmt_delta(t_cur.clicks, t_prev.clicks)}), impressions {t_cur.impressions:.0f} "
        f"({fmt_delta(t_cur.impressions, t_prev.impressions)}), average position "
        f"{fmt_pos(t_cur.position)} ({fmt_delta(t_cur.position, t_prev.position, lower_is_better=True)})",
        "",
        "| Blog page | Clicks | Δ | Impressions | Δ | Position | Δ | CTR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for path in blog[:limit]:
        c, o = cur[path], prev.get(path)
        lines.append(
            f"| {path} | {c.clicks:.0f} | {fmt_delta(c.clicks, o.clicks if o else None)} | "
            f"{c.impressions:.0f} | {fmt_delta(c.impressions, o.impressions if o else None)} | "
            f"{fmt_pos(c.position)} | "
            f"{fmt_delta(c.position, o.position if o else None, lower_is_better=True)} | "
            f"{(c.ctr or 0) * 100:.2f}% |"
        )
    if len(blog) > limit:
        lines.append(
            f"\n{len(blog) - limit} more blog pages with impressions are not shown."
        )
    movers: list[dict] = []
    for path in blog:
        c, o = cur[path], prev.get(path)
        if (
            not o
            or o.impressions < MIN_IMPRESSIONS_FOR_MOVE
            or c.impressions < MIN_IMPRESSIONS_FOR_MOVE
        ):
            continue
        assert c.position is not None and o.position is not None
        movers.append(
            {
                "path": path,
                "position_change": c.position - o.position,
                "before": o,
                "after": c,
            }
        )
    movers.sort(key=lambda m: m["position_change"])
    if movers:
        lines += ["", "**Biggest movers** (both windows >= 50 impressions)", ""]
        for m in movers[:5] + [m for m in movers[-5:] if m not in movers[:5]]:
            lines.append(
                f"- {m['path']}: position {fmt_pos(m['before'].position)} -> "
                f"{fmt_pos(m['after'].position)}, clicks {m['before'].clicks:.0f} -> "
                f"{m['after'].clicks:.0f}"
            )
    return "\n".join(lines), movers

"""The one chart component (PLAN step 10a): every chart the Hub draws, of any type, on one scale function.

Pedro's chart rules (2026-11-19, docs/design/ux-workflow-and-screens.md):
1. The axis shows its real values (ticks with numbers).
2. The floor is computed from the numbers, never set by hand, and never exaggerates: it shows the difference (40% of
   the spread below the smallest value) but a visual change is at most MAX_EXAGGERATION times the real change, then
   lowered to a tick; if it reaches zero the axis starts at zero.
3. The current month shows only what has happened so far (no forecast, no budget). A past month shows its full month
   and the value by the same day of the month as today: columns as a tinted block with a solid part; the history line
   as a pale line of full months and a strong line of same-day values; horizontal bars as a tinted track (last month in
   full) and a solid fill (this month so far). A trendline is a dashed straight fit drawn over the data only.
4. Flat base, rounded ends, no outlines, no legend, faint gridlines; pale for the past, strong for the present.
5. Measurements over time: the printed reference range is a pale band, a value outside it is red, the range is written
   under the chart.
Ported from the mock-up generator (`docs/design/mockups/_build/build_mockups.py`: make_scale, col_chart, line_chart,
hbar_chart). The drawing takes no budget and no forecast: there is nowhere to pass one."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import Callable, Optional, Sequence

from markupsafe import Markup

MAX_EXAGGERATION = 1.5   # a visual change may be at most this many times the real change (the "lie factor")

# Earnings green, spending orange (Option A); "bad" for a measurement outside its range.
COLOURS = {"e": dict(empty="#E6EEE1", old="#B7D0B5", now="#5E8C61"),
           "s": dict(empty="#FBEADD", old="#F3C9A8", now="#E5632B")}
BAD_NOW, BAD_OLD, BAND = "#C8452D", "#E9ADA1", "#EAF0E6"


@dataclass(frozen=True)
class Item:
    """One column or point. `full`: the whole month (past months only; None for the current month or a measurement);
    `value`: the month by the same day as today, the current month so far, or the measured value."""
    label: str
    value: float
    full: Optional[float] = None
    current: bool = False
    sub: str = ""


@dataclass(frozen=True)
class Scale:
    floor: float
    top: float
    ticks: tuple

    def at(self, v: float, size: float) -> float:
        return 0.0 if self.top == self.floor else size * (v - self.floor) / (self.top - self.floor)


def nice_up(x: float) -> float:
    """Smallest of 1, 2, 2.5, 5, 10 times a power of ten that is >= x."""
    if x <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(x))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if x <= m * mag * (1 + 1e-9))


def _tidy(values: list) -> tuple:
    if all(abs(v - round(v)) < 1e-9 for v in values):
        return tuple(int(round(v)) for v in values)
    return tuple(round(v, 6) for v in values)


def make_scale(values: Sequence[float], n_ticks: int = 4) -> Scale:
    """(floor, top, ticks). Nothing is set per chart. With negative values (a net flow) the axis spans them and zero."""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return Scale(0, 1, (0, 1))
    lo_v, hi_v = min(vals), max(vals)
    if lo_v < 0:
        top = max(hi_v, 0.0)
        step = nice_up((top - lo_v) / n_ticks)
        lo = math.floor(lo_v / step + 1e-9) * step
        ticks = [lo + k * step for k in range(int((top - lo) / step + 1e-9) + 1)]
        t = _tidy([lo] + ticks)
        return Scale(t[0], top if top != lo else lo + step, t[1:])
    span = hi_v - lo_v
    cand = min(lo_v - 0.4 * span, lo_v * (1 - 1 / MAX_EXAGGERATION)) if span > 0 else 0
    if cand <= 0:
        lo, step = 0.0, nice_up(hi_v / n_ticks) if hi_v > 0 else 1
    else:
        step = nice_up((hi_v - cand) / n_ticks)
        lo = math.floor(cand / step + 1e-9) * step
    ticks = [lo + k * step for k in range(int((hi_v - lo) / step + 1e-9) + 1)]
    t = _tidy([lo] + ticks)
    top = hi_v if hi_v > t[0] else t[0] + step
    return Scale(t[0], top, t[1:])


def exaggeration(scale: Scale, values: Sequence[float]) -> float:
    """How many times the drawn change between the smallest and largest value overstates the real one (1 = none)."""
    vals = [v for v in values if v is not None and v > 0]
    if len(vals) < 2 or min(vals) == max(vals) or scale.floor <= 0:
        return 1.0
    lo, hi = min(vals), max(vals)
    return ((hi - scale.floor) / (lo - scale.floor)) / (hi / lo)


def euros(v: float, cents: bool = False) -> str:
    if cents:                                    # a balance or a bill, to the cent
        return f"€{v:,.2f}"
    return f"€{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"€{v:,.2f}"


def plain(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")


def _top_round(x, y, w, h, r=14):
    r = max(0.0, min(r, h, w / 2))
    return (f"M{x:.1f},{y+h:.1f} L{x:.1f},{y+r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x+r:.1f},{y:.1f} L{x+w-r:.1f},{y:.1f} "
            f"A{r:.1f},{r:.1f} 0 0 1 {x+w:.1f},{y+r:.1f} L{x+w:.1f},{y+h:.1f} Z")


def _right_round(x, y, w, h, r):
    w = max(w, 0.0)
    r = max(0.0, min(r, w, h / 2))
    return (f"M{x:.1f},{y:.1f} L{x+w-r:.1f},{y:.1f} A{r:.1f},{r:.1f} 0 0 1 {x+w:.1f},{y+r:.1f} L{x+w:.1f},{y+h-r:.1f} "
            f"A{r:.1f},{r:.1f} 0 0 1 {x+w-r:.1f},{y+h:.1f} L{x:.1f},{y+h:.1f} Z")


def _range_text(ref, fmt) -> str:
    a, b = ref
    if a is not None and b is not None:
        return f"reference range {fmt(a)} to {fmt(b)}"
    return f"reference: up to {fmt(b)}" if b is not None else f"reference: from {fmt(a)}"


def _outside(ref, v) -> bool:
    return bool(ref) and v is not None and ((ref[0] is not None and v < ref[0]) or (ref[1] is not None and v > ref[1]))


def _axis(out, scale: Scale, Y, x_l, x_r, fmt):
    for i, tk in enumerate(scale.ticks):
        y = Y(tk)
        out.append(f'<line x1="{x_l}" x2="{x_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1" '
                   f'opacity="{1 if i == 0 or tk == 0 else .55}"/>')
        out.append(f'<text x="{x_l-6}" y="{y+3.5:.1f}" text-anchor="end" font-size="9.5" fill="var(--muted)">'
                   f'{escape(fmt(tk))}</text>')


def _band(out, ref, scale: Scale, Y, top, base, x_l, x_r):
    if not ref:
        return
    rlo, rhi = ref
    if (rhi is not None and rhi < scale.floor) or (rlo is not None and rlo > scale.top):
        return
    yt = top if (rhi is None or rhi >= scale.top) else Y(rhi)
    yb = base if (rlo is None or rlo <= scale.floor) else Y(rlo)
    out.append(f'<rect x="{x_l}" y="{yt:.1f}" width="{x_r-x_l}" height="{max(yb-yt, 0):.1f}" fill="{BAND}"/>')


def _values(items: Sequence[Item]) -> list[float]:
    return [v for it in items for v in (it.full, it.value) if v is not None]


def columns(items: Sequence[Item], *, kind: str = "s", fmt: Callable = euros, w: int = 330, h: int = 190,
            ref=None, title: str = "") -> Markup:
    """Columns. A past month: a tinted block (full month) with a solid part to the same day; the current month: the
    solid part only. A measurement (no full value): a solid column."""
    if not items:
        return Markup("")
    c = COLOURS[kind]
    scale = make_scale(_values(items))                         # the reference band shows only inside the axis
    n = len(items)
    ml, base, top = 44, 150, 22
    pitch = (w - ml - 8) / n
    bw = min(72, pitch * 0.6)
    x0 = ml + (pitch - bw) / 2
    small = n > 6 or bw < 36
    zero = base - scale.at(0, base - top) if scale.floor < 0 else base
    Y = lambda v: base - scale.at(v, base - top)
    fs_top, fs_in, fs_m = (11, 13, 12) if not small else (9.5, 10.5, 10)
    uid = f"c{abs(hash((tuple(items), kind, w))) % 10**8}"
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{escape(title)}">']
    _band(out, ref, scale, Y, top, base, ml, w - 8)
    _axis(out, scale, Y, ml, w - 8, fmt)
    for i, it in enumerate(items):
        x = x0 + i * pitch
        if it.full is not None and it.full >= 0 and scale.floor >= 0:
            y = Y(it.full)
            cid = f"{uid}-{i}"
            ly = Y(it.value)
            out.append(f'<clipPath id="{cid}"><path d="{_top_round(x, y, bw, base - y)}"/></clipPath>')
            out.append(f'<path d="{_top_round(x, y, bw, base - y)}" fill="{c["empty"]}"/>')
            out.append(f'<rect x="{x:.1f}" y="{ly:.1f}" width="{bw:.1f}" height="{max(base-ly, 0):.1f}" '
                       f'fill="{c["old"]}" clip-path="url(#{cid})"/>')
            out.append(f'<text x="{x+bw/2:.1f}" y="{y-6:.1f}" text-anchor="middle" font-size="{fs_top}" '
                       f'fill="var(--muted)">{escape(fmt(it.full))}</text>')
            if not small:
                out.append(f'<text x="{x+bw/2:.1f}" y="{ly+fs_in+7:.1f}" text-anchor="middle" font-size="{fs_in}" '
                           f'font-weight="700" fill="var(--ink)">{escape(fmt(it.value))}</text>')
        else:
            v = it.value
            bad = _outside(ref, v)
            fill = (BAD_NOW if it.current else BAD_OLD) if bad else (c["now"] if it.current else c["old"])
            if v >= 0:
                ly = Y(max(v, scale.floor))
                out.append(f'<path d="{_top_round(x, ly, bw, max(zero - ly, 0))}" fill="{fill}"/>')
                label_y = ly - 6 if small or zero - ly < fs_in + 10 else ly + fs_in + 7
            else:
                ly = Y(v)
                out.append(f'<rect x="{x:.1f}" y="{zero:.1f}" width="{bw:.1f}" height="{max(ly - zero, 0):.1f}" '
                           f'rx="3" fill="{BAD_NOW if it.current else BAD_OLD}"/>')
                label_y = ly + fs_in + 4
            inside = not small and v >= 0 and zero - ly >= fs_in + 10
            colour = "#fff" if inside and it.current else "var(--ink)"
            if it.current or not small:
                out.append(f'<text x="{x+bw/2:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="{fs_in}" '
                           f'font-weight="700" fill="{colour}">{escape(fmt(v))}</text>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{base+19}" text-anchor="middle" font-size="{fs_m}" '
                   f'font-weight="{700 if it.current else 400}" fill="var(--ink)">{escape(it.label)}</text>')
        if it.sub:
            out.append(f'<text x="{x+bw/2:.1f}" y="{base+32}" text-anchor="middle" font-size="9" fill="var(--muted)">'
                       f'{escape(it.sub)}</text>')
    if ref:
        out.append(f'<text x="{ml}" y="{base+(46 if any(i.sub for i in items) else 36)}" font-size="9.5" '
                   f'fill="var(--muted)">{escape(_range_text(ref, fmt))}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def line(items: Sequence[Item], *, kind: str = "s", fmt: Callable = euros, w: int = 560, h: int = 200, ref=None,
         trend: bool = False, per: str = "per month", title: str = "") -> Markup:
    """History line: a strong line of the same-day (or measured) values ending today; a pale line of full months.
    trend=True adds a dashed straight-line fit through the strong points, drawn over the data only."""
    if not items:
        return Markup("")
    c = COLOURS[kind]
    scale = make_scale(_values(items))
    n = len(items)
    ml, mr, base, top = 44, 16, 150, 26
    xs = [ml + (i + .5) * (w - ml - mr) / n for i in range(n)]
    Y = lambda v: base - scale.at(v, base - top)
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{escape(title)}">']
    _band(out, ref, scale, Y, top, base, ml, w - mr)
    _axis(out, scale, Y, ml, w - mr, fmt)
    fulls = [(xs[i], Y(it.full)) for i, it in enumerate(items) if it.full is not None]
    if len(fulls) > 1:
        out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in fulls)}" fill="none" '
                   f'stroke="{c["old"]}" stroke-width="2"/>')
    for x, y in fulls:
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{c["old"]}"/>')
    pts = [(xs[i], Y(it.value)) for i, it in enumerate(items)]
    if n > 1:
        out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" '
                   f'stroke="{c["now"]}" stroke-width="2.6" stroke-linejoin="round"/>')
    if trend and n > 1:
        ys = [it.value for it in items]
        mx, my = (n - 1) / 2, sum(ys) / n
        slope = sum((i - mx) * (v - my) for i, v in enumerate(ys)) / sum((i - mx) ** 2 for i in range(n))
        icpt = my - slope * mx
        out.append(f'<line x1="{xs[0]:.1f}" y1="{Y(icpt):.1f}" x2="{xs[-1]:.1f}" y2="{Y(icpt + slope * (n - 1)):.1f}" '
                   f'stroke="var(--ink)" stroke-width="1.6" stroke-dasharray="6 4" opacity=".7"/>')
        out.append(f'<text x="{w-mr}" y="{top-10}" text-anchor="end" font-size="10.5" fill="var(--ink)">trend '
                   f'{"▲" if slope > 0 else "▼"} {escape(fmt(abs(slope)))} {escape(per)}</text>')
    for i, it in enumerate(items):
        x, y = pts[i]
        bad = _outside(ref, it.value)
        col = BAD_NOW if bad else c["now"]
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if it.current else 3.4}" fill="{col}"/>')
        if it.current or n <= 6:
            out.append(f'<text x="{x:.1f}" y="{y-9:.1f}" text-anchor="middle" font-size="{11 if n <= 6 else 10}" '
                       f'font-weight="700" fill="{col if bad else "var(--ink)"}">{escape(fmt(it.value))}</text>')
        if n <= 8 or i % 2 == (n - 1) % 2:
            out.append(f'<text x="{x:.1f}" y="{base+19}" text-anchor="middle" font-size="{11 if n <= 8 else 10}" '
                       f'font-weight="{700 if it.current else 400}" fill="var(--ink)">{escape(it.label)}</text>')
        if it.sub:
            out.append(f'<text x="{x:.1f}" y="{base+32}" text-anchor="middle" font-size="9" fill="var(--muted)">'
                       f'{escape(it.sub)}</text>')
    if ref:
        out.append(f'<text x="{ml}" y="{base+(46 if any(i.sub for i in items) else 36)}" font-size="9.5" '
                   f'fill="var(--muted)">{escape(_range_text(ref, fmt))}</text>')
    out.append("</svg>")
    return Markup("".join(out))


@dataclass(frozen=True)
class Bar:
    """One horizontal bar: this month so far, and last month in full (the tinted track)."""
    label: str
    now: float
    last: float
    link: Optional[str] = None


def bars(rows: Sequence[Bar], *, fmt: Callable = euros, w: int = 440, rowh: int = 28, label_w: int = 96,
         right_w: int = 120, last_label: str = "last month", title: str = "") -> Markup:
    """Horizontal bars on the same scale rule, with a real x axis."""
    rows = [r for r in rows if r.now is not None]
    if not rows:
        return Markup("")
    c = COLOURS["s"]
    scale = make_scale([v for r in rows for v in (r.now, r.last) if v is not None and v >= 0] or [0])
    pl, pr, top = label_w + 8, w - right_w, 8
    X = lambda v: pl + scale.at(max(v, scale.floor), pr - pl)
    bottom = top + len(rows) * rowh
    out = [f'<svg viewBox="0 0 {w} {bottom+26}" class="chart" role="img" aria-label="{escape(title)}">']
    for i, tk in enumerate(scale.ticks):
        x = X(tk)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{bottom}" stroke="var(--line)" '
                   f'opacity="{1 if i == 0 else .55}"/>')
        out.append(f'<text x="{x:.1f}" y="{bottom+14}" text-anchor="middle" font-size="9.5" fill="var(--muted)">'
                   f'{escape(fmt(tk))}</text>')
    for i, r in enumerate(rows):
        y = top + i * rowh + 4
        bh = rowh - 8
        label = escape(r.label if len(r.label) <= 16 else r.label[:15] + "…")
        if r.link:
            label = f'<a href="{escape(r.link)}">{label}</a>'
        out.append(f'<text x="{label_w}" y="{y+bh/2+4:.1f}" text-anchor="end" font-size="11.5" fill="var(--ink)">'
                   f'{label}</text>')
        if r.last:
            out.append(f'<path d="{_right_round(pl, y, X(r.last)-pl, bh, bh/2)}" fill="{c["empty"]}"/>')
        out.append(f'<path d="{_right_round(pl, y, X(r.now)-pl, bh, bh/2)}" fill="{c["now"]}"/>')
        out.append(f'<text x="{pr+8}" y="{y+bh/2+4:.1f}" font-size="11.5" font-weight="700" fill="var(--ink)">'
                   f'{escape(fmt(r.now))}<tspan font-weight="400" fill="var(--muted)" font-size="10"> '
                   f'{escape(last_label)} {escape(fmt(r.last or 0))}</tspan></text>')
    out.append("</svg>")
    return Markup("".join(out))


__all__ = ["MAX_EXAGGERATION", "Item", "Bar", "Scale", "make_scale", "exaggeration", "nice_up", "columns", "line",
           "bars", "euros", "plain"]

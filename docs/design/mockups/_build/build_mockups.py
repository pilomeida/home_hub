#!/usr/bin/env python3
"""Builds the three Hub UI mock-up sheets (static HTML, sample data, no gradients, no faces)."""
import os, sys

OUT = sys.argv[1]

# ---------------------------------------------------------------- tokens
TOK = {
 "A": dict(name="Warm and soft", bg="#FAF3EA", surface="#FFFFFF", t1="#FFE6D3", t2="#E3EBDA", ink="#2A2019",
           muted="#8A7B6E", line="#EDE0D2", accent="#E5632B", accent2="#5E8C61", bar="#F3C9A8", radius="22px",
           fs="15px", pad="20px", gap="16px", good="#4F8A55", bad="#C8452D"),
 "B": dict(name="Calm workspace", bg="#F2F4F1", surface="#FFFFFF", t1="#E6F1EA", t2="#F6F7F5", ink="#17231C",
           muted="#6B7A71", line="#E3E8E4", accent="#1F7A4D", accent2="#9BCDB0", bar="#9BCDB0", radius="16px",
           fs="14px", pad="18px", gap="14px", good="#1F7A4D", bad="#C0392B"),
 "C": dict(name="Dense and precise", bg="#F6F7F9", surface="#FFFFFF", t1="#F1F4FA", t2="#F9FAFB", ink="#101828",
           muted="#667085", line="#E4E7EC", accent="#2F5BD8", accent2="#E0398A", bar="#7F9CF0", radius="8px",
           fs="13px", pad="14px", gap="10px", good="#12935B", bad="#D92D20"),
}

PITCH = {
 "A": "Friendly and roomy: large rounded cards, big numbers, hatched bars. The top bar is a row of pills. Best on a phone and for the children; the desk work is a little less dense.",
 "B": "A left sidebar and white cards with one green accent. The familiar workspace layout; grows easily as categories are added; balanced between phone and desk.",
 "C": "Top tabs, small type, tables and charts first, with hover values and bulk selection. The best operator desk; the phone version trades density for a short list.",
}

NAV = [("home", "Home", "⌂"), ("money", "Money", "€"), ("health", "Health", "+"), ("things", "Things", "▣"),
       ("papers", "Papers", "▤"), ("coming", "Coming up", "◷"), ("waiting", "Waiting for you", "●")]
ACTIVE = {"home": "home", "ask": "home", "home_alt": "home", "money": "money", "history": "money", "waiting": "waiting",
          "review": "waiting", "merchants": "waiting", "health": "health", "person": "health", "thing": "things",
          "coming": "coming", "settings": None}

# ---------------------------------------------------------------- small components
def chip(t, kind=""): return f'<span class="chip {kind}">{t}</span>'
def btn(t, kind=""): return f'<button class="btn {kind}">{t}</button>'
def card(inner, cls="", title=None, right=""):
    h = f'<div class="card-h"><span>{title}</span><span class="card-r">{right}</span></div>' if title else ""
    return f'<section class="card {cls}">{h}{inner}</section>'

def ranked(rows, unit="€", maxv=None, pct=False):
    maxv = maxv or max(v for _, v, *_ in rows)
    out = ['<div class="ranked">']
    for r in rows:
        label, v = r[0], r[1]
        extra = r[2] if len(r) > 2 else ""
        w = int(100 * v / maxv)
        val = f"{v}%" if pct else f"{unit}{v:,}"
        out.append(f'<div class="rk"><div class="rk-l">{label}</div><div class="rk-t"><i style="width:{w}%"></i>'
                   f'{"<b></b>" if extra else ""}</div><div class="rk-v">{val}</div></div>')
    out.append('</div>')
    return "".join(out)

def bars(vals, labels, w=520, h=170, hi=None, hatch_before=None, budget=None):
    n = len(vals); m = max(vals + ([budget] if budget else [])) * 1.1
    bw = w / n; out = [f'<svg viewBox="0 0 {w} {h+22}" class="chart">']
    for i, v in enumerate(vals):
        bh = h * v / m; x = i * bw + bw * .18; y = h - bh
        fill = "var(--accent)" if i == hi else ("url(#hatch)" if (hatch_before is not None and i < hatch_before) else "var(--bar)")
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*.64:.1f}" height="{bh:.1f}" rx="{min(8, bw*.2):.1f}" fill="{fill}"/>')
        out.append(f'<text x="{i*bw+bw/2:.1f}" y="{h+16}" text-anchor="middle" class="ax">{labels[i]}</text>')
    if budget:
        y = h - h * budget / m
        out.append(f'<line x1="0" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" class="bl"/>')
        out.append(f'<text x="{w-2}" y="{y-5:.1f}" text-anchor="end" class="ax">budget €{budget}</text>')
    out.append('</svg>')
    return "".join(out)

def line(series, labels, w=520, h=170, tip=None, budget=None, fill=False, band=None):
    allv = [v for s in series for v in s[1]] + ([budget] if budget else []) + (list(band) if band else [])
    mx, mn = max(allv) * 1.08, min(allv) * .9
    n = len(labels)
    def pt(i, v): return (i * (w - 20) / (n - 1) + 10, h - (v - mn) / (mx - mn) * h)
    out = [f'<svg viewBox="0 0 {w} {h+22}" class="chart">']
    for g in range(1, 4):
        y = h * g / 4; out.append(f'<line x1="0" x2="{w}" y1="{y:.0f}" y2="{y:.0f}" class="gl"/>')
    if band:
        yh, yl = pt(0, band[1])[1], pt(0, band[0])[1]
        out.append(f'<rect x="0" y="{yh:.1f}" width="{w}" height="{yl-yh:.1f}" fill="url(#hatchlight)"/><text x="4" y="{yh+11:.1f}" class="ax">reference range {band[0]}–{band[1]}</text>')
    if budget:
        y = pt(0, budget)[1]
        out.append(f'<line x1="0" x2="{w}" y1="{y:.1f}" y2="{y:.1f}" class="bl"/>')
        out.append(f'<text x="{w-2}" y="{y-5:.1f}" text-anchor="end" class="ax">budget €{budget}</text>')
    for k, (name, vals, cls) in enumerate(series):
        pts = " ".join(f"{pt(i, v)[0]:.1f},{pt(i, v)[1]:.1f}" for i, v in enumerate(vals))
        if fill and k == 0:
            out.append(f'<polygon points="{pt(0,mn)[0]:.1f},{h} {pts} {pt(n-1,mn)[0]:.1f},{h}" fill="url(#hatchlight)"/>')
        out.append(f'<polyline points="{pts}" class="ln {cls}"/>')
    step = max(1, n // 6)
    for i in range(0, n, step):
        out.append(f'<text x="{pt(i,0)[0]:.1f}" y="{h+16}" text-anchor="middle" class="ax">{labels[i]}</text>')
    if tip is not None:
        i, v, lab = tip; x, y = pt(i, v)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h}" class="gl dash"/><circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="dot"/>')
        out.append(f'<g transform="translate({min(x+8, w-130):.1f},{max(y-44,2):.1f})"><rect width="124" height="38" rx="6" class="tipbox"/>'
                   f'<text x="9" y="16" class="tipt">{lab[0]}</text><text x="9" y="31" class="tipt b">{lab[1]}</text></g>')
    out.append('</svg>')
    return "".join(out)

def spark(vals, w=90, h=26, bad=False):
    mx, mn = max(vals), min(vals)
    pts = " ".join(f"{i*(w-4)/(len(vals)-1)+2:.1f},{h-2-(v-mn)/(mx-mn or 1)*(h-4):.1f}" for i, v in enumerate(vals))
    return f'<svg viewBox="0 0 {w} {h}" class="spark"><polyline points="{pts}" class="ln a"/></svg>'

def table(head, rows, cb=False, cls=""):
    th = ("<th class=cb></th>" if cb else "") + "".join(f"<th>{h}</th>" for h in head)
    body = ""
    for r in rows:
        sel = r[0] == "*"; cells = r[1:] if sel else r
        body += "<tr class='sel'>" if sel else "<tr>"
        if cb: body += f"<td class=cb><span class='box{' on' if sel else ''}'></span></td>"
        body += "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    return f'<table class="tbl {cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'

def tabs(items, active):
    return '<div class="tabs">' + "".join(f'<span class="tab{" on" if t == active else ""}">{t}</span>' for t in items) + '</div>'

def ask_input(placeholder="Ask the Hub, or drop a file here", big=False):
    return (f'<div class="askbox{" big" if big else ""}"><span class="plus" title="Attach a file or take a photo">+</span>'
            f'<span class="ph">{placeholder}</span><span class="kbd">⌘K</span></div>')

def doc_placeholder(h=420):
    lines = "".join(f'<i style="width:{w}%"></i>' for w in (62, 88, 74, 91, 55, 83, 70, 90, 48, 79, 86, 60))
    return (f'<div class="paper" style="height:{h}px"><div class="paper-h"><b>AEG</b><span>Fatura FT 2026/4471</span></div>{lines}'
            f'<div class="paper-t"><span>Total</span><b>649,00 €</b></div></div>')

# ---------------------------------------------------------------- shell per option
def header_tools(o):
    return ('<span class="who">Who: Everyone ▾</span>' + '<span class="add">+ Add a paper</span>'
            '<span class="gear" title="Settings">⚙</span><span class="alert" title="Problems: 1 needs you">1</span>')

def shell(o, key, body, title):
    act = ACTIVE[key]
    if o == "A":
        pills = "".join(f'<span class="pill{" on" if k == act else ""}">{l}{" <em>11</em>" if k == "waiting" else ""}</span>' for k, l, _ in NAV)
        top = (f'<header class="topnav"><div class="logo"><b>◐</b> Home Hub</div><nav class="pills">{pills}</nav>'
               f'<div class="tools">{header_tools(o)}</div></header>')
        sub = "" if key in ("home", "home_alt") else f'<div class="subbar">{ask_input("Ask or search…")}</div>'
        return f'<div class="frame A">{top}<main class="content">{sub}{body}</main></div>'
    if o == "B":
        items = "".join(f'<div class="sbi{" on" if k == act else ""}"><span class="ic">{ic}</span>{l}{"<em>11</em>" if k == "waiting" else ""}</div>' for k, l, ic in NAV)
        side = (f'<aside class="side"><div class="logo"><b>◐</b> Home Hub</div><div class="sbl">MENU</div>{items}'
                f'<div class="grow"></div><div class="sbi"><span class="ic">⚙</span>Settings</div>'
                f'<div class="sbi alertrow"><span class="ic">!</span>1 problem needs you</div></aside>')
        topbar = (f'<div class="mtop">{"<span class=grow></span>" if key == "home" else ask_input("Ask or search…")}<span class="who">Who: Everyone ▾</span>'
                  f'<span class="add">+ Add a paper</span></div>')
        return f'<div class="frame B">{side}<div class="mainw">{topbar}<main class="content">{body}</main></div></div>'
    tabs_ = "".join(f'<span class="ttab{" on" if k == act else ""}">{l}{" <em>11</em>" if k == "waiting" else ""}</span>' for k, l, _ in NAV)
    top = (f'<header class="topbar"><div class="logo"><b>◐</b> Home Hub</div>{"<span class=grow></span>" if key == "home" else ask_input("Ask or search…")}'
           f'<div class="tools">{header_tools(o)}</div></header><div class="ttabs">{tabs_}</div>')
    return f'<div class="frame C">{top}<main class="content">{body}</main></div>'

# ---------------------------------------------------------------- screens
MONTHS = ["Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
GROC = [548, 562, 530, 585, 574, 560, 598, 541, 566, 590, 571, 512]
SPEND = [2310, 2450, 2290, 2380, 2520, 2410, 2600, 2350, 2290, 2480, 2530, 2184]

def coming_rows():
    return [("Fri 2026-11-21", "Vodafone bill", "€42.00", chip("Money")),
            ("Tue 2026-11-24", "Dentist appointment", "10:30", chip("Health")),
            ("2026-12-03", "Car insurance renews", "€388.20", chip("Insurance")),
            ("2027-01-16", "Passport expires: Rute", "58 days", chip("Identity")),
            ("2027-03-31", "IUC, car 1", "€61.40", chip("Vehicles"))]

def coming_list(o, n=5):
    out = '<ul class="cl">'
    for d, t, v, c in coming_rows()[:n]:
        out += f'<li><span class="cd">{d}</span><span class="ct">{t}</span><span class="cv">{v}</span>{c}</li>'
    return out + '</ul>'

def waiting_card(o):
    return (f'<div class="wait"><div><div class="big">11</div><div class="sub">waiting for you</div></div>'
            f'<div class="wl"><span>6 papers</span><span>3 statements</span><span>2 health</span></div>{btn("Review", "pri")}</div>')


def top_round(x, y, w, h, r=14):
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y+h:.1f} L{x:.1f},{y+r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x+r:.1f},{y:.1f} L{x+w-r:.1f},{y:.1f} "
            f"A{r:.1f},{r:.1f} 0 0 1 {x+w:.1f},{y+r:.1f} L{x+w:.1f},{y+h:.1f} Z")

import math

# ---- THE chart rule (Pedro, 2026-11-19). One scale, one set of rules, every chart type, including those Ask draws.
MAX_EXAGGERATION = 1.5   # a visual change may be at most this many times the real change (the "lie factor")

def nice_up(x):
    """Smallest of 1, 2, 2.5, 5, 10 times a power of ten that is >= x."""
    if x <= 0: return 1
    mag = 10 ** math.floor(math.log10(x))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if x <= m * mag * (1 + 1e-9))

def make_scale(vals, n_ticks=4):
    """Returns (floor, top, ticks). The floor shows the difference (40% of the spread below the smallest value) but is
    capped so a visual change never exceeds MAX_EXAGGERATION x the real change: floor <= smallest x (1 - 1/MAX).
    The floor is then lowered to a tick. If it reaches zero, the scale starts at zero. Nothing is set per chart."""
    lo_v, hi_v = min(vals), max(vals); span = hi_v - lo_v
    cand = min(lo_v - 0.4 * span, lo_v * (1 - 1 / MAX_EXAGGERATION)) if span > 0 else 0
    if cand <= 0:
        lo = 0; tstep = nice_up(hi_v / n_ticks)
    else:
        tstep = nice_up((hi_v - cand) / n_ticks); lo = math.floor(cand / tstep + 1e-9) * tstep
    ticks = [lo + k * tstep for k in range(int((hi_v - lo) / tstep + 1e-9) + 1)]
    if all(abs(x - round(x)) < 1e-9 for x in [lo] + ticks): lo = int(round(lo)); ticks = [int(round(x)) for x in ticks]
    else: lo = round(lo, 6); ticks = [round(x, 6) for x in ticks]
    return lo, hi_v, ticks

def _vals(items): return [v for _, f, b, _ in items for v in (f, b) if v is not None]
def _eur(v): return f"€{v:,}"
def range_txt(ref, fmt):
    a, b = ref
    return f"reference range {fmt(a)} to {fmt(b)}" if a is not None and b is not None else (f"reference: up to {fmt(b)}" if b is not None else f"reference: from {fmt(a)}")
def _oor(ref, v): return bool(ref) and ((ref[0] is not None and v < ref[0]) or (ref[1] is not None and v > ref[1]))
COL = {"e": dict(empty="#E6EEE1", old="#B7D0B5", now="#5E8C61"), "s": dict(empty="#FBEADD", old="#F3C9A8", now="#E5632B")}
BAD_NOW, BAD_OLD = "#C8452D", "#E9ADA1"

def _axis(out, ticks, H, base, x_l, x_r, fmt):
    for tk in ticks:
        y = base - H(tk)
        out.append(f'<line x1="{x_l}" x2="{x_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--line)" stroke-width="1" opacity="{1 if tk == ticks[0] else .55}"/>')
        out.append(f'<text x="{x_l-6}" y="{y+3.5:.1f}" text-anchor="end" font-size="9.5" fill="var(--muted)">{fmt(tk)}</text>')

def _band(out, ref, lo, hi, H, base, top, x_l, x_r):
    if not ref: return
    rlo, rhi = ref
    if (rhi is not None and rhi < lo) or (rlo is not None and rlo > hi): return
    yt = top if (rhi is None or rhi >= hi) else base - H(rhi)
    yb = base if (rlo is None or rlo <= lo) else base - H(rlo)
    out.append(f'<rect x="{x_l}" y="{yt:.1f}" width="{x_r-x_l}" height="{yb-yt:.1f}" fill="#EAF0E6"/>')

def col_chart(kind, items, w=330, h=190, fmt=None, inside="all", subs=None, ref=None, **_ignored):
    """items = (label, full_month_or_None, value_by_day_X, is_current). A past month is a tinted block (its full month)
    with a solid part up to the same day as today; the current month is its solid part only (no forecast, no budget).
    Flat base, rounded top, no outlines, no legend; a real y-axis with values."""
    fmt = fmt or _eur; c = COL[kind]
    lo, hi, ticks = make_scale(_vals(items))
    n = len(items)
    ml, base, top = 44, 150, 22
    pitch = (w - ml - 8) / n; bw = min(72, pitch * 0.6); x0 = ml + (pitch - bw) / 2
    small = n > 6 or bw < 36
    H = lambda v: (base - top) * (v - lo) / (hi - lo)
    fs_top, fs_in, fs_m = (11, 13, 12) if not small else (9.5, 10.5, 10)
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart bottle">']
    _band(out, ref, lo, hi, H, base, top, ml, w - 8)
    _axis(out, ticks, H, base, ml, w - 8, fmt)
    for i, (m, full, by, now) in enumerate(items):
        x = x0 + i * pitch; cid = f"cc{kind}{n}{i}{w}"; ly = base - H(by)
        if full is not None:
            y = base - H(full)
            out.append(f'<clipPath id="{cid}"><path d="{top_round(x, y, bw, H(full))}"/></clipPath>')
            out.append(f'<path d="{top_round(x, y, bw, H(full))}" fill="{c["empty"]}"/>')
            out.append(f'<rect x="{x:.1f}" y="{ly:.1f}" width="{bw:.1f}" height="{base-ly:.1f}" fill="{c["old"]}" clip-path="url(#{cid})"/>')
            out.append(f'<text x="{x+bw/2:.1f}" y="{y-6:.1f}" text-anchor="middle" font-size="{fs_top}" fill="var(--muted)">{fmt(full)}</text>')
            if inside == "all" and full > 0:
                out.append(f'<text x="{x+bw/2:.1f}" y="{ly+fs_in+7:.1f}" text-anchor="middle" font-size="{fs_in}" font-weight="700" fill="var(--ink)">{fmt(by)}</text>')
        else:
            oor = _oor(ref, by)
            fillc = (BAD_NOW if now else BAD_OLD) if oor else (c["now"] if now else c["old"])
            out.append(f'<path d="{top_round(x, ly, bw, base-ly)}" fill="{fillc}"/>')
            if small and now:
                out.append(f'<text x="{x+bw/2:.1f}" y="{ly-6:.1f}" text-anchor="middle" font-size="{fs_in}" font-weight="700" fill="var(--ink)">{fmt(by)}</text>')
            elif inside in ("all", "now") or not small:
                out.append(f'<text x="{x+bw/2:.1f}" y="{ly+fs_in+7:.1f}" text-anchor="middle" font-size="{fs_in}" font-weight="700" fill="{"#fff" if now else "var(--ink)"}">{fmt(by)}</text>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{base+19}" text-anchor="middle" font-size="{fs_m}" font-weight="{700 if now else 400}" fill="var(--ink)">{m}</text>')
        if subs: out.append(f'<text x="{x+bw/2:.1f}" y="{base+32}" text-anchor="middle" font-size="9" fill="var(--muted)">{subs[i]}</text>')
    if ref: out.append(f'<text x="{ml}" y="{base+(46 if subs else 36)}" font-size="9.5" fill="var(--muted)">{range_txt(ref, fmt)}</text>')
    out.append('</svg>')
    return "".join(out)

bottle_chart = col_chart

def line_chart(kind, items, w=560, h=200, fmt=None, ref=None, trend=False, subs=None, per="per month"):
    """History line. Strong line = value by the same day of each month (like for like, ending at today); pale line =
    the full month, past months only. Measurements (no full month) are one strong line. No forecast, no budget.
    trend=True adds a dashed straight-line fit through the strong points, drawn only over the data, never beyond it."""
    fmt = fmt or _eur; c = COL[kind]
    lo, hi, ticks = make_scale(_vals(items))
    n = len(items); ml, mr, base, top = 44, 16, 150, 26
    xs = [ml + (i + .5) * (w - ml - mr) / n for i in range(n)]
    H = lambda v: (base - top) * (v - lo) / (hi - lo); Y = lambda v: base - H(v)
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart">']
    _band(out, ref, lo, hi, H, base, top, ml, w - mr)
    _axis(out, ticks, H, base, ml, w - mr, fmt)
    fulls = [(xs[i], Y(f)) for i, (_, f, _, _) in enumerate(items) if f is not None]
    if len(fulls) > 1:
        out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in fulls)}" fill="none" stroke="{c["old"]}" stroke-width="2"/>')
    for x, y in fulls: out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{c["old"]}"/>')
    pts = [(xs[i], Y(b)) for i, (_, _, b, _) in enumerate(items)]
    out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{c["now"]}" stroke-width="2.6" stroke-linejoin="round"/>')
    if trend:
        ys = [b for _, _, b, _ in items]; mx_ = (n - 1) / 2; my_ = sum(ys) / n
        sl = sum((i - mx_) * (v - my_) for i, v in enumerate(ys)) / sum((i - mx_) ** 2 for i in range(n)); ic = my_ - sl * mx_
        out.append(f'<line x1="{xs[0]:.1f}" y1="{Y(ic):.1f}" x2="{xs[-1]:.1f}" y2="{Y(ic + sl * (n - 1)):.1f}" stroke="var(--ink)" stroke-width="1.6" stroke-dasharray="6 4" opacity=".7"/>')
        arrow = "▲" if sl > 0 else "▼"
        av = abs(sl); av = int(round(av)) if av >= 5 else round(av, 1)
        out.append(f'<text x="{w-mr}" y="{top-10}" text-anchor="end" font-size="10.5" fill="var(--ink)">trend {arrow} {fmt(av)} {per}</text>')
    for i, (m, f, b, now) in enumerate(items):
        x, y = pts[i]; bad = _oor(ref, b)
        col = BAD_NOW if bad else c["now"]
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if now else 3.4}" fill="{col}"/>')
        if now or n <= 6:
            out.append(f'<text x="{x:.1f}" y="{y-9:.1f}" text-anchor="middle" font-size="{11 if n <= 6 else 10}" font-weight="700" fill="{col if bad else "var(--ink)"}">{fmt(b)}</text>')
        if n <= 8 or i % 2 == (n - 1) % 2:
            out.append(f'<text x="{x:.1f}" y="{base+19}" text-anchor="middle" font-size="{11 if n <= 8 else 10}" font-weight="{700 if now else 400}" fill="var(--ink)">{m}</text>')
        if subs: out.append(f'<text x="{x:.1f}" y="{base+32}" text-anchor="middle" font-size="9" fill="var(--muted)">{subs[i]}</text>')
    if ref: out.append(f'<text x="{ml}" y="{base+(46 if subs else 36)}" font-size="9.5" fill="var(--muted)">{range_txt(ref, fmt)}</text>')
    out.append('</svg>')
    return "".join(out)

def _right_round(x, y, w, h, r):
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f} L{x+w-r:.1f},{y:.1f} A{r:.1f},{r:.1f} 0 0 1 {x+w:.1f},{y+r:.1f} L{x+w:.1f},{y+h-r:.1f} "
            f"A{r:.1f},{r:.1f} 0 0 1 {x+w-r:.1f},{y+h:.1f} L{x:.1f},{y+h:.1f} Z")

def hbar_chart(rows, w=440, fmt=None, rowh=28, label_w=74, right_w=112, prev_label="Oct"):
    """Horizontal bars. rows = (label, value so far this month, last month's full value). The tinted track is last
    month's full value, the solid fill is this month so far; same scale rule and a real x-axis with values."""
    fmt = fmt or _eur; c = COL["s"]
    rows = sorted(rows, key=lambda r: -r[1])
    lo, hi, ticks = make_scale([v for _, a, b in rows for v in (a, b)])
    pl, pr, top = label_w + 8, w - right_w, 8
    X = lambda v: pl + (pr - pl) * (v - lo) / (hi - lo)
    bottom = top + len(rows) * rowh
    out = [f'<svg viewBox="0 0 {w} {bottom+26}" class="chart">']
    for tk in ticks:
        x = X(tk)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{bottom}" stroke="var(--line)" opacity="{1 if tk == ticks[0] else .55}"/>')
        out.append(f'<text x="{x:.1f}" y="{bottom+14}" text-anchor="middle" font-size="9.5" fill="var(--muted)">{fmt(tk)}</text>')
    for i, (lab, now, last) in enumerate(rows):
        y = top + i * rowh + 4; bh = rowh - 8
        out.append(f'<text x="{label_w}" y="{y+bh/2+4:.1f}" text-anchor="end" font-size="11.5" fill="var(--ink)">{lab}</text>')
        out.append(f'<path d="{_right_round(pl, y, X(last)-pl, bh, bh/2)}" fill="{c["empty"]}"/>')
        out.append(f'<path d="{_right_round(pl, y, X(now)-pl, bh, bh/2)}" fill="{c["now"]}"/>')
        out.append(f'<text x="{pr+8}" y="{y+bh/2+4:.1f}" font-size="11.5" font-weight="700" fill="var(--ink)">{fmt(now)}<tspan font-weight="400" fill="var(--muted)" font-size="10"> {prev_label} {fmt(last)}</tspan></text>')
    out.append('</svg>')
    return "".join(out)

def ranked_mtd(rows, w=440):
    return hbar_chart(rows, w=w, label_w=70 if w >= 400 else 56, right_w=112 if w >= 400 else 96)

SPEND_BY = [2080, 2200, 2060, 2140, 2270, 2170, 2340, 2115, 2060, 2190, 2277]
GROC_BY = [430, 440, 415, 460, 450, 438, 470, 425, 445, 478, 449]
SPEND_ITEMS = [(MONTHS[i], SPEND[i], SPEND_BY[i], False) for i in range(11)] + [("Nov", None, 2184, True)]
GROC_ITEMS = [(MONTHS[i], GROC[i], GROC_BY[i], False) for i in range(11)] + [("Nov", None, 512, True)]
GROUPS = [("Food", 612, 655), ("Housing", 480, 480), ("Transport", 210, 248), ("Health", 188, 220), ("Utilities", 176, 190), ("Leisure", 124, 150), ("Other", 394, 587)]

def money_block_A(title, kind, items, delta):
    now = items[-1]
    return (f'<div class="mcol"><div class="mmh"><span class="mmt">{title}</span><span class="sub">so far in November</span></div>'
            f'<div class="mmb"><span class="big s">€{now[2]:,}</span>{chip(delta, "ok")}</div>{bottle_chart(kind, items)}</div>')

def money_card_A():
    e = money_block_A("Earnings", "e", [("Sep", 4120, 2860, False), ("Oct", 4310, 2940, False), ("Nov", None, 2980, True)], "▲ 1.4% vs Oct, same date")
    s = money_block_A("Spending", "s", [("Sep", 2480, 2190, False), ("Oct", 2530, 2277, False), ("Nov", None, 2184, True)], "▼ 4.1% vs Oct, same date")
    av = ('<div class="avail"><div><div class="sub">Available balance</div><div class="big s">€14,382.17</div></div>'
          '<div class="avail-r"><div>' + chip("matches the bank ✓ 07:30", "ok") + '</div>'
          '<div class="sub">After the 5 bills due this month (€336.50): <b>€14,045.67</b></div></div></div>')
    return card(f'<div class="mm">{e}{s}</div>{av}', "", "Money this month", "Open ›")

def waiting_row_A():
    items = [("t1", "Inbox", "6", "papers", "Review"), ("t2", "Statements", "3", "don’t add up", "Check"),
             ("t1", "Health", "2", "to confirm", "Confirm"), ("t2", "Merchants", "1,430", "unconfirmed", "Open")]
    cells = "".join(f'<div class="wi {k}"><div><div class="wi-a">{a}</div><div><span class="big s">{n}</span> <span class="sub">{u}</span></div></div>'
                    f'{btn(b, "pri" if i == 0 else "")}</div>' for i, (k, a, n, u, b) in enumerate(items))
    return card(f'<div class="wrow2">{cells}</div>', "", "Waiting for you · 11", "Open the queue ›")

def s_home(o):
    money_big = '<div class="big">€2,184</div><div class="sub">spent of €2,650 budget · 82%</div>'
    if o == "A":
        hero = card('<div class="hero-t">What would you like to know, or do?</div>' + ask_input("Ask the Hub, or drop a file here", True), "hero tint1")
        return (f'<div class="homeA">{hero}<div class="g2a">{money_card_A()}{card(coming_list(o, 5), "", "Coming up", "See all ›")}</div>{waiting_row_A()}</div>')
    if o == "B":
        hero = card(ask_input("Ask the Hub, or drop a file here", True), "")
        kp = '<div class="kp3">' + "".join(f'<div class="kp"><div class="sub">{a}</div><div class="big s">{b}</div><div class="d {k}">{d}</div></div>'
              for a, b, d, k in [("Spent this month", "€2,184", "−4.1% vs Oct", "good"), ("Budget left", "€466", "18 days to go", ""), ("Cash balance", "€14,382", "matches the bank ✓", "good")]) + '</div>'
        m = card(kp + ranked([("Food", 612), ("Housing", 480), ("Transport", 210), ("Health", 188), ("Utilities", 176)]), "", "Money this month", "Open ›")
        c = card(coming_list(o, 5), "", "Coming up", "See all ›")
        w = card(waiting_card(o), "wcard dark")
        return f'<div class="homeB">{hero}<div class="gB"><div>{m}</div><div class="col">{c}{w}</div></div></div>'
    hero = card(ask_input("Ask the Hub, or drop a file here  ·  e.g. “history of Groceries, last 12 months”", True), "")
    ks = [("Spent", "€2,184", "−4.1%", "good", SPEND[-6:]), ("Budget", "82%", "of €2,650", "", [60, 64, 70, 74, 79, 82]),
          ("Cash", "€14,382", "matches ✓", "good", [14100, 14250, 14000, 14380, 14290, 14382]), ("Due soon", "€186", "3 bills", "", [90, 120, 80, 160, 140, 186]),
          ("Loans", "3 open", "1 red flag", "bad", [3, 3, 3, 3, 3, 3]), ("Waiting", "11", "6 · 3 · 2", "", [4, 6, 9, 8, 10, 11])]
    strip = '<div class="kstrip">' + "".join(f'<div class="kc"><div class="sub">{a}</div><div class="kv">{b}</div><div class="kd {k}">{d}</div>{spark(sp)}</div>' for a, b, d, k, sp in ks) + '</div>'
    ch = card(line([("Spent", SPEND, "a"), ("Last year", [2250, 2300, 2200, 2310, 2400, 2350, 2500, 2280, 2240, 2390, 2450, 2300], "g")], MONTHS, 560, 190, tip=(10, 2530, ("Oct 2026", "€2,530 · last yr €2,450")), budget=2650), "", "Spending vs budget · 12 months", "Spending ›")
    cu = card(table(["Date", "What", "Amount", "Area"], [[a, b, c_, d] for a, b, c_, d in coming_rows()]), "", "Coming up", "See all ›")
    return f'{hero}{strip}<div class="g2 c"><div>{ch}</div><div>{cu}</div></div>'

def ask_thread(o):
    ans1 = card(
        '<div class="q">How much did we spend on groceries last month?</div>'
        '<div class="a"><b>€571 on Groceries in October: 94% of the €608 budget.</b></div>'
        + (col_chart("s", GROC_ITEMS[-6:], 780, 200) if o == "A" else bars(GROC[-6:], MONTHS[-6:], 420, 110, hi=4, budget=608) if o == "B" else line([("Groceries", GROC, "a")], MONTHS, 420, 110, budget=608, tip=(10, 571, ("Oct 2026", "€571 · 94%"))))
        + '<div class="src">Read from 31 bank lines (Continente, Pingo Doce, Lidl) · ' + btn("Open the page") + btn("Save to wiki") + '</div>', "ans")
    ans2 = card('<div class="q">Add a to-do: renew Rute’s passport</div><div class="a">Added, due 2026-12-16.</div>'
                '<div class="src"><span class="toast">Added to Coming up · ' + "<u>Undo</u></span></div>", "ans")
    ans3 = card('<div class="q">Approve these 6 papers in the Inbox</div>'
                '<div class="confirm"><div class="ct">This will file 6 papers. It cannot be undone from here.</div>'
                + table(["Paper", "Filed as", "Level"], [["AEG dishwasher invoice", "House › Warranty", "Mid"], ["EDP bill 2026-11", "Money › Bill", "Mid"], ["Lab result, 2026-11", "Health › Lab", "High"], ["Vodafone bill 2026-11", "Money › Bill", "Mid"], ["Gym receipt", "Money › Bill", "Low"], ["School letter", "Education", "Mid"]], cls="mini")
                + '<div class="btns">' + btn("Do it, file 6", "pri") + btn("Cancel") + '</div></div>', "ans")
    att = card('<div class="attach"><span class="file">▤ IMG_2041.jpg</span><span class="sub">sent to the Inbox as a suggestion. Not filed yet.</span> ' + btn("Review it now") + '</div>', "ans")
    return f'<div class="thread">{ans1}{ans2}{ans3}{att}<div class="stickyask">{ask_input("Ask the Hub, or drop a file here")}</div></div>'

def s_ask(o): return ask_thread(o)

def s_money(o):
    head = [("Spent this month", "€2,184", "of €2,650 budget · 82%", ""), ("Cash balance", "€14,382.17", "matches Santander ✓ 07:30", "good"),
            ("Due soon", "€186", "3 bills this week", ""), ("Loans", "3 open", "1 red flag: Matias", "bad")]
    kp = '<div class="g4 k">' + "".join(card(f'<div class="sub">{a}</div><div class="big s">{b}</div><div class="d {k}">{d}</div>') for a, b, d, k in head) + '</div>'
    tb = tabs(["Spending", "Bills & due", "Loans", "Utilities", "All lines"], "Spending")
    rk = card(ranked_mtd(GROUPS) if o == "A" else ranked([("Food", 612), ("Housing", 480), ("Transport", 210), ("Health", 188), ("Utilities", 176), ("Leisure", 124), ("Other", 394)]), "", "By group · November", "Pick a month ▾")
    if o == "A": tr = card(col_chart("s", SPEND_ITEMS, 560, 200, inside="now"), "", "12 months", "Last 12 ▾")
    elif o == "B": tr = card(line([("Spent", SPEND, "a")], MONTHS, 560, 190, budget=2650, fill=True), "", "12 months", "Last 12 ▾")
    else: tr = card(line([("Spent", SPEND, "a"), ("Last year", [2250, 2300, 2200, 2310, 2400, 2350, 2500, 2280, 2240, 2390, 2450, 2300], "g")], MONTHS, 560, 190, budget=2650, tip=(8, 2290, ("Aug 2026", "€2,290 · last yr €2,240"))), "", "12 months · toggle series", "Last 12 ▾")
    link = '<div class="hint">Want one category over time? ' + btn("Open category history ›") + '</div>'
    return f'{kp}{tb}<div class="g2 m"><div>{rk}</div><div>{tr}{link}</div></div>'

def s_history(o):
    ctl = ('<div class="ctl"><span class="sel">Category: <b>Food › Groceries</b> ▾</span><span class="sel">Last <b>12</b> months ▾</span>'
           '<span class="sel">View: <b>Columns</b> ▾</span><span class="sp"></span>' + btn("Ask about this") + '</div>')
    if o == "A": ch = col_chart("s", GROC_ITEMS, 760, 210, inside="now")
    else: ch = line([("Groceries", GROC, "a")], MONTHS, 760, 210, budget=608, tip=(10, 571, ("Oct 2026", "€571 · 94% of budget")))
    tab = table(["Month", "Spent", "Budget", "vs budget", "Top merchant", ""], [
        ["Nov (so far)", "€512", "€608", chip("84%", "ok"), "Continente", "Lines ›"], ["Oct 2026", "€571", "€608", chip("94%", "warn"), "Continente", "Lines ›"],
        ["Sep 2026", "€590", "€608", chip("97%", "warn"), "Pingo Doce", "Lines ›"], ["Aug 2026", "€566", "€608", chip("93%", "warn"), "Continente", "Lines ›"],
        ["Jul 2026", "€541", "€608", chip("89%", "ok"), "Lidl", "Lines ›"]])
    return f'{ctl}{card(ch, "", "Groceries · last 12 months", "average €562 · highest Jun €598")}{card(tab, "", "By month")}'

def s_waiting(o):
    tb = tabs(["All", "Inbox", "Statements", "Health", "Merchants"], "All")
    rows = [
        ("Inbox", "6 papers", "Suggested a category for each. Approve, change or discard.", "Open the first ›", "oldest 6 days"),
        ("Statements", "3 don’t add up", "Santander 2026-10 is €12.40 off. Check the highlighted lines.", "Open the first ›", ""),
        ("Health", "2 to confirm", "A new test name and a new topic from the lab paper of 2026-11.", "Open the first ›", ""),
        ("Merchants", "1,430 unconfirmed", "Best guess filled in. Select many, confirm in one go.", "Open the table ›", "bulk"),
    ]
    inner = "".join(f'<div class="wrow"><div class="wa">{a}</div><div class="wb"><b>{b}</b><div class="sub">{c}</div></div><div class="wd">{e}</div>{btn(d, "pri" if i == 0 else "")}</div>'
                    for i, (a, b, c, d, e) in enumerate(rows))
    note = '<div class="hint">The menu count (11) is papers, statements and health. Merchants have their own line because 1,430 would hide everything else.</div>'
    return f'{tb}{card(inner, "wlist")}{note}'

def s_review(o):
    left = card(doc_placeholder(440), "", "The paper", "Download · 2 pages")
    fields = [("Supplier", "AEG"), ("Date", "2026-11-12"), ("Amount", "€649.00"), ("Filed as", "House › Appliance"), ("Item name", "missing: the invoice does not say. Type it."), ("Warranty ends", "2028-11-12  (24 months, read from the invoice)"), ("Privacy level", "Mid")]
    fr = "".join(f'<div class="fld{" need" if "needed" in v else ""}"><label>{k}</label><div class="fv">{v}</div></div>' for k, v in fields)
    right = card(fr + '<div class="sugg">Why this suggestion: the invoice names an AEG dishwasher; House › Appliance has a warranty reader.</div>'
                 '<div class="btns">' + btn("Approve  ⏎", "pri") + btn("Edit") + btn("Later  L") + btn("Discard  ⌫") + '</div>'
                 '<div class="kbdh">⏎ approve · E edit · L later · J / K next / previous</div>', "", "What the Hub read", "2 of 6")
    return f'<div class="g2 rv"><div>{left}</div><div>{right}</div></div>'

def s_merchants(o):
    rows = [["*", "AMIGO (VODAFONE PORTUGAL)", "Vodafone", "Utilities › Telecom", "38", chip("99%", "ok")],
            ["*", "CONTINENTE MODELO 0231", "Continente", "Food › Groceries", "211", chip("98%", "ok")],
            ["*", "PINGO DOCE 114 LISBOA", "Pingo Doce", "Food › Groceries", "164", chip("97%", "ok")],
            ["*", "GALP 8821", "Galp", "Transport › Fuel", "57", chip("96%", "ok")],
            ["", "WWW.PAYPAL.COM *SPOTIFY", "Spotify", "Leisure › Subscriptions", "14", chip("91%", "ok")],
            ["", "MB WAY JOAO F.", "?", "Other", "9", chip("42%", "warn")],
            ["", "PAG SERV 21056", "Segurança Social", "Taxes", "12", chip("100%", "ok")]]
    rows = [r[1:] if r[0] == "" else r for r in rows]
    t = table(["Bank text", "Best guess", "Category", "Lines", "Confidence"], rows, cb=True)
    bar = ('<div class="bulk"><span><b>4 selected</b> · 470 lines</span><span class="sp"></span>' + btn("Confirm 4", "pri") + btn("Change category ▾") + btn("Skip") + '</div>')
    top = tabs(["Confidence ≥ 95%  (812)", "Needs a look  (618)"], "Confidence ≥ 95%  (812)")
    return f'{top}{bar}{card(t, "flush")}<div class="hint">Confirming writes one standing rule per merchant, so the next line is filed without asking.</div>'

def s_health(o):
    ppl = [("Pedro", "Next: blood pressure check 2027-02", "Latest: lab result 2026-09"), ("Rute", "Next: dentist Tue 2026-11-24", "Latest: lab result 2026-11"),
           ("Child 1", "Next: paediatric check-up 2026-12-10", "Latest: vaccination 2026-06"), ("Child 2", "Nothing due", "Latest: lab result 2025-11")]
    tiles = "".join(card(f'<div class="pn">{n}</div><div class="sub">{a}</div><div class="sub">{b}</div>', "ptile") for n, a, b in ppl)
    strip = card(table(["Due", "Who", "What", ""], [["2026-11-24", "Rute", "Dentist", chip("in 5 days")], ["2026-12-10", "Child 1", "Paediatric check-up", chip("in 21 days")], ["2026-09-30", "Pedro", "Repeat blood test", chip("overdue", "bad")]]), "", "Follow-ups across the family", "All ›")
    return f'<div class="g4 p">{tiles}</div>{strip}{card(table(["Area", "Papers"], [["Blood and lab", "22"], ["Dental", "9"], ["Eyes", "4"], ["Vaccinations", "11"]], cls="mini"), "", "Or browse by body area or topic", "Sample data")}'

def s_person(o):
    cond = card('<div class="cond"><b>Seasonal allergy</b><span class="sub">3 papers · 1 visit · medication: antihistamine</span></div>'
                '<div class="cond"><b>Growth check</b><span class="sub">4 papers · next check-up 2026-12-10</span></div>'
                + '<div class="btns">' + btn("Propose a new condition") + '</div>', "", "Conditions", "Sample data")
    ch = line([("Hemoglobin", [12.1, 12.4, 12.8, 12.6, 12.9], "a")], ["2023-11", "2024-11", "2025-03", "2025-11", "2026-03"], 420, 140, band=(11.5, 15.5), tip=(4, 12.9, ("2026-03 · HSM", "12.9 g/dL (11.5–15.5)")))
    if o == "A":
        ch = col_chart("e", [("2023-11", None, 12.1, False), ("2024-11", None, 12.4, False), ("2025-03", None, 12.8, False), ("2025-11", None, 12.6, False), ("2026-03", None, 12.9, True)],
                       420, 200, fmt=lambda v: f"{v:.1f}", subs=["Cintramédica", "Cintramédica", "HSM", "HSM", "HSM"],
                       ref=(11.5, 15.5))
    res = card('<div class="sub">Hemoglobin in g/dL: the same test from two labs, one line.</div>' + ch, "", "Latest results", "Reference range 11.5–15.5")
    tl = card('<ul class="tl"><li><b>2026-12-10</b> Paediatric check-up <i>booked</i></li><li><b>2026-06-14</b> Vaccination record filed</li>'
              '<li><b>2026-03-02</b> Lab result, HSM</li><li><b>2025-11-20</b> Visit note: allergist</li></ul>', "", "Timeline", "Filter: area ▾ topic ▾")
    return f'<div class="ph1"><span class="pn big">Child 1</span><span class="sub">sample data · 11 papers</span></div><div class="g3 pp"><div>{cond}</div><div>{res}</div><div>{tl}</div></div>'

def s_thing(o):
    hdr = '<div class="ph1"><span class="pn big">Car 1</span><span class="sub">Thing · vehicle · sample data</span></div>'
    tb = tabs(["Overview", "Papers", "Money", "Reminders"], "Overview")
    due = card(table(["Date", "What", ""], [["2026-12-03", "Insurance renews", chip("€388.20")], ["2027-03-31", "IUC road tax", chip("€61.40")], ["2027-05-14", "Inspection (IPO)", ""], ["2026-06-02", "Last service", chip("done")]], cls="mini"), "", "Dates and reminders")
    pol = card('<div class="cond"><b>Car insurance</b><span class="sub">Third-party + glass · policy 0482-1177 · €388.20 / year</span></div><div class="cond"><b>IUC 2026</b><span class="sub">paid 2026-03-28</span></div>', "", "Insurance and taxes")
    pap = card(table(["Paper", "Date", "Level"], [["Insurance premium notice", "2026-11-04", "Mid"], ["Service invoice", "2026-06-02", "Mid"], ["IUC receipt", "2026-03-28", "Mid"], ["Registration document", "2022-04-12", "High"]], cls="mini"), "", "Every paper about it", "Filter: category ▾")
    cost = card(ranked([("Fuel", 1280), ("Insurance", 388), ("Service", 214), ("Tax", 61)]) , "", "Cost in the last 12 months", "")
    return f'{hdr}{tb}<div class="g2 th"><div>{due}{pol}</div><div>{pap}{cost}</div></div>'

def s_coming(o):
    tb = tabs(["Dates", "To-dos"], "Dates")
    rows = [[a, b, c, d] for a, b, c, d in coming_rows()] + [["2027-02-10", "Blood pressure check: Pedro", "—", chip("Health")], ["2027-06-30", "Dishwasher warranty check", "—", chip("House")]]
    chips = '<div class="chips">' + "".join(chip(x, "on" if x == "All" else "") for x in ["All", "Money", "Health", "House", "Insurance", "Identity", "Vehicles", "Education"]) + '</div>'
    return f'{tb}{chips}{card(table(["Date", "What", "Amount / note", "Area"], rows), "flush")}'

def s_settings(o):
    pr = card('<div class="prob"><div class="pw"><b>Not synced.</b></div><div class="pw">Santander says Rute’s access has expired.</div><div class="pw">Renew it in the Santander app, then press Reconnect.</div>'
              + btn("Reconnect", "pri") + '</div>', "", "Problems", "1 open")
    lv = card(table(["Kind of paper", "Level"], [["Bills", chip("Mid")], ["Lab results", chip("High")], ["Warranties", chip("Low")], ["Contracts", chip("Mid")]], cls="mini"), "", "Privacy levels", "Edit ›")
    jb = card(table(["Job", "When"], [["Bank sync", "07:30 · 13:00 · 18:30"], ["Mailbox check", "every 10 min"], ["Backup", "nightly"]], cls="mini"), "", "Scheduled jobs", "Edit ›")
    bk = card(table(["Connection", "State"], [["Santander · Pedro", chip("ok", "ok")], ["Santander · Rute", chip("expired", "bad")], ["Revolut", chip("ok", "ok")]], cls="mini"), "", "Bank connections", "Manage ›")
    wk = card('<div class="sub">The standing facts Ask reads from (not a place to browse): 214 facts, 31 changed this month.</div>' + btn("Open What the Hub knows ›"), "", "What the Hub knows")
    return f'<div class="g2 st"><div>{pr}{bk}</div><div>{lv}{jb}{wk}</div></div>'

# phone screens
def phone(o, inner, active):
    nav = [("Home", "⌂"), ("Money", "€"), ("+", "+"), ("Health", "+"), ("Things", "▣")]
    nv = "".join(f'<span class="pn2{" on" if n == active else ""}{" fab" if n == "+" else ""}">{"" if n == "+" else i + "<br>"}{n if n != "+" else "+"}</span>' for n, i in nav)
    return f'<div class="phone {o}"><div class="pscreen">{inner}</div><div class="pbar">{nv}</div></div>'

def p_home(o):
    return phone(o, ('<div class="phd"><b>◐ Home Hub</b><span class="alert">1</span></div>'
                     + ask_input("Ask, or drop a file")
                     + card('<div class="big s">€2,184</div><div class="sub">of €2,650 · cash matches the bank ✓</div>' + (ranked_mtd(GROUPS[:3], 300) if o == "A" else ranked([("Food", 612), ("Housing", 480), ("Transport", 210)])), "", "Money")
                     + card(coming_list(o, 3), "", "Coming up")
                     + '<div class="wchip"><b>11</b> waiting for you · <u>Review</u></div>'), "Home")

def p_ask(o):
    return phone(o, ('<div class="phd"><b>Ask</b></div><div class="q">Approve this paper</div>'
                     '<div class="card ans"><div class="a"><b>It’s an AEG invoice, €649.00, House › Appliance.</b></div>'
                     '<div class="confirm"><div class="ct">File it as a dishwasher with a warranty to 2028-11-12?</div><div class="btns">' + btn("Do it", "pri") + btn("Cancel") + '</div></div></div>'
                     '<div class="stickyask">' + ask_input("Ask, or attach") + '</div>'), "Home")

def p_approve(o):
    return phone(o, ('<div class="phd"><b>Waiting</b><span class="sub">1 of 6</span></div>'
                     + '<div class="pdoc">' + doc_placeholder(150) + '</div>'
                     + '<div class="pf"><label>Supplier</label><b>AEG</b></div><div class="pf"><label>Amount</label><b>€649.00</b></div>'
                       '<div class="pf"><label>Filed as</label><b>House › Appliance</b></div><div class="pf need"><label>Item name</label><b>missing: type it</b></div>'
                       '<div class="btns">' + btn("Approve", "pri") + btn("Edit") + btn("Later") + '</div>'), "Home")

# ---------------------------------------------------------------- CSS
BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:#E7E7EA;color:#222}
.sheet-h{max-width:1240px;margin:0 auto;padding:28px 24px 8px}
.sheet-h h1{font-size:26px}.sheet-h p{max-width:760px;margin:6px 0 12px;color:#444;line-height:1.45}
.sw{display:flex;gap:6px;margin:8px 0}.sw i{width:34px;height:34px;border-radius:8px;border:1px solid rgba(0,0,0,.15)}
.idx{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 0}.idx a{font-size:13px;padding:5px 10px;border-radius:999px;background:#fff;color:#222;text-decoration:none;border:1px solid #ccc}
.sheet-m{max-width:1240px;margin:0 auto;padding:12px 24px 80px}
.cap{margin:34px 0 8px;font-size:14px;font-weight:600;display:flex;gap:10px;align-items:baseline}.cap span{font-weight:400;color:#555}
.note{margin:8px 0 0;font-size:13px;color:#444;max-width:900px;line-height:1.45;background:#fff;border-left:3px solid #999;padding:8px 12px;border-radius:4px}
.frame-w{width:100%}
.phones{display:flex;gap:28px;flex-wrap:wrap;align-items:flex-start}
.phone-w{display:flex;flex-direction:column}
.chart{width:100%;height:auto;display:block;overflow:visible}.chart .ax{font-size:9.5px;fill:var(--muted)}
.chart .gl{stroke:var(--line);stroke-width:1}.chart .gl.dash{stroke-dasharray:3 3}.chart .bl{stroke:var(--accent2);stroke-width:1.3;stroke-dasharray:5 4}
.chart .ln{fill:none;stroke-width:2.4;stroke-linejoin:round}.chart .ln.a{stroke:var(--accent)}.chart .ln.g{stroke:var(--muted);stroke-dasharray:4 4;stroke-width:1.6}
.chart .dot{fill:#fff;stroke:var(--accent);stroke-width:2.5}.chart .tipbox{fill:var(--ink)}.chart .tipt{fill:#fff;font-size:10px}.chart .tipt.b{font-weight:700}
.sub + .btn{display:inline-block;margin-top:10px}.spark{width:100%;height:26px;margin-top:4px}.spark .ln{fill:none;stroke:var(--accent);stroke-width:1.8}
"""

FRAME_CSS = """
.frame{--sc:1;width:1180px;min-height:760px;background:var(--bg);color:var(--ink);font-size:var(--fs);border-radius:var(--radius);overflow:hidden;position:relative;zoom:var(--z,1)}
.frame *, .phone *{letter-spacing:0}
.frame .content{padding:var(--pad)}
.card{background:var(--surface);border-radius:var(--radius);padding:var(--pad);margin-bottom:var(--gap)}
.card.flush{padding:0;overflow:hidden}
.card-h{display:flex;justify-content:space-between;align-items:baseline;font-weight:600;margin-bottom:12px}.card-r{font-weight:400;font-size:.85em;color:var(--muted)}
.big{font-size:2.6em;font-weight:600;line-height:1.1}.big.s{font-size:1.9em}.sub{color:var(--muted);font-size:.88em}
.d{font-size:.85em;margin-top:2px;color:var(--muted)}.d.good,.kd.good{color:var(--good)}.d.bad,.kd.bad{color:var(--bad)}
.chip{display:inline-block;font-size:.8em;padding:2px 9px;border-radius:999px;background:var(--t2);border:1px solid var(--line);white-space:nowrap}
.chip.ok{color:var(--good);border-color:var(--good)}.chip.warn{color:#B7791F;border-color:#B7791F}.chip.bad{color:var(--bad);border-color:var(--bad)}.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.btn{font:inherit;font-size:.9em;padding:7px 14px;border-radius:calc(var(--radius) * .6);border:1px solid var(--line);background:var(--surface);color:var(--ink);margin-right:6px;cursor:default}
.btn.pri{background:var(--accent);color:#fff;border-color:var(--accent)}
.grow{flex:1}.sp{flex:1}
.g2,.g3,.g4{display:grid;gap:var(--gap)}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:1fr 1fr 1fr}.g4{grid-template-columns:repeat(4,1fr)}
.g2.c{grid-template-columns:1.25fr 1fr}.g2.m{grid-template-columns:1fr 1.5fr}.g2.rv{grid-template-columns:1fr 1fr}.g3.pp{grid-template-columns:1fr 1.3fr 1fr}.g2.th{grid-template-columns:1fr 1fr}
.logo{font-weight:700;display:flex;gap:8px;align-items:center}.logo b{color:var(--accent)}
.askbox{display:flex;align-items:center;gap:10px;background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--radius) * .8);padding:10px 14px;color:var(--muted);flex:1;min-width:0}
.askbox .plus{width:26px;height:26px;border-radius:50%;border:1px solid var(--line);display:grid;place-items:center;color:var(--accent);flex:none}
.askbox .ph{flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.askbox .kbd{font-size:.75em;border:1px solid var(--line);border-radius:5px;padding:1px 6px}
.askbox.big{padding:18px 20px;font-size:1.15em}
.who,.add,.gear{font-size:.88em;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--surface);white-space:nowrap}
.add{background:var(--accent);color:#fff;border-color:var(--accent)}.gear{width:32px;text-align:center;padding:5px 0}
.alert{display:inline-grid;place-items:center;min-width:20px;height:20px;border-radius:50%;background:var(--bad);color:#fff;font-size:.75em;font-weight:700}
em{font-style:normal;font-size:.75em;background:var(--accent);color:#fff;border-radius:999px;padding:1px 7px;margin-left:6px}
.ranked .rk{display:grid;grid-template-columns:90px 1fr 70px;gap:10px;align-items:center;margin:9px 0;font-size:.92em}
.rk-t{height:12px;background:var(--t2);border-radius:99px;overflow:hidden}.rk-t i{display:block;height:100%;background:var(--bar);border-radius:99px}.rk-v{text-align:right;color:var(--muted)}
.cl{list-style:none}.cl li{display:grid;grid-template-columns:108px 1fr auto auto;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line)}.cl li:last-child{border:0}
.cd{color:var(--muted);font-size:.85em}.cv{font-weight:600}
.wait{display:flex;flex-direction:column;gap:12px}.wl{display:flex;gap:6px;flex-wrap:wrap}.wl span{font-size:.85em;border:1px solid var(--line);border-radius:99px;padding:2px 10px}
.foot{display:flex;gap:10px;align-items:center;margin-top:10px;font-size:.88em}
.hero-t{font-size:1.5em;font-weight:600;margin-bottom:14px}
.tbl{width:100%;border-collapse:collapse;font-size:.92em}.tbl th{text-align:left;font-weight:500;color:var(--muted);padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.tbl td{padding:10px;border-bottom:1px solid var(--line)}.tbl tr:last-child td{border:0}.tbl.mini td,.tbl.mini th{padding:7px 8px}
.tbl tr.sel td{background:var(--t1)}.cb{width:30px}.box{display:inline-block;width:15px;height:15px;border-radius:4px;border:1.5px solid var(--muted);vertical-align:middle}.box.on{background:var(--accent);border-color:var(--accent);box-shadow:inset 0 0 0 2px #fff}
.tabs{display:flex;gap:6px;margin:0 0 var(--gap)}.tab{padding:7px 14px;color:var(--muted);font-size:.92em}.tab.on{color:var(--ink);font-weight:600}
.chips{display:flex;gap:6px;margin-bottom:var(--gap);flex-wrap:wrap}
.hint{font-size:.88em;color:var(--muted);margin:2px 0 var(--gap)}
.ctl{display:flex;gap:10px;align-items:center;margin-bottom:var(--gap)}.sel{background:var(--surface);border:1px solid var(--line);border-radius:calc(var(--radius) * .6);padding:7px 12px;font-size:.92em}
.thread{max-width:820px;margin:0 auto}.q{font-weight:600;margin-bottom:6px}.a{margin-bottom:10px}.src{display:flex;gap:6px;align-items:center;font-size:.85em;color:var(--muted);margin-top:8px;flex-wrap:wrap}
.toast{background:var(--ink);color:#fff;border-radius:99px;padding:5px 14px}.toast u{margin-left:10px;cursor:default}
.confirm{border:1.5px solid var(--accent);border-radius:calc(var(--radius) * .7);padding:12px;margin-top:8px}.confirm .ct{font-weight:600;margin-bottom:8px}.btns{margin-top:12px}
.attach{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.file{border:1px solid var(--line);border-radius:8px;padding:4px 10px;background:var(--t2)}
.stickyask{margin-top:6px;display:flex}
.wrow{display:grid;grid-template-columns:110px 1fr 100px auto;gap:14px;align-items:center;padding:16px 4px;border-bottom:1px solid var(--line)}.wrow:last-child{border:0}.wa{font-weight:600}.wd{color:var(--muted);font-size:.85em}
.paper{background:#fff;border:1px solid var(--line);border-radius:6px;padding:18px;display:flex;flex-direction:column;gap:9px}.paper i{display:block;height:7px;background:#E5E5E5;border-radius:4px}
.paper-h{display:flex;justify-content:space-between;margin-bottom:6px;font-size:.9em}.paper-t{display:flex;justify-content:space-between;margin-top:auto;border-top:1px solid var(--line);padding-top:8px}
.fld{display:grid;grid-template-columns:120px 1fr;padding:9px 0;border-bottom:1px solid var(--line)}.fld label{color:var(--muted)}.fld.need .fv{color:var(--bad);font-weight:600}
.sugg,.kbdh{font-size:.85em;color:var(--muted);margin-top:10px}
.bulk{display:flex;gap:8px;align-items:center;background:var(--ink);color:#fff;border-radius:calc(var(--radius) * .7);padding:10px 14px;margin-bottom:var(--gap)}.bulk .btn{color:var(--ink)}
.pn{font-weight:600;font-size:1.15em}.pn.big{font-size:1.7em}.ptile{min-height:110px}.ph1{display:flex;gap:14px;align-items:baseline;margin-bottom:var(--gap)}
.cond{display:flex;flex-direction:column;padding:9px 0;border-bottom:1px solid var(--line);gap:2px}.cond:last-of-type{border:0}
.tl{list-style:none}.tl li{padding:9px 0 9px 16px;border-left:2px solid var(--line);margin-left:4px;font-size:.92em}.tl i{color:var(--muted);font-style:normal;margin-left:6px}
.prob{display:flex;flex-direction:column;gap:5px}.pw:nth-child(2){color:var(--muted)}.prob .btn{align-self:flex-start;margin-top:8px}
.kp3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px}
/* phone */
.phone{width:375px;height:780px;border:10px solid #1b1b1d;border-radius:44px;background:var(--bg);color:var(--ink);font-size:14px;display:flex;flex-direction:column;overflow:hidden}
.pscreen{flex:1;padding:16px;overflow:hidden;display:flex;flex-direction:column;gap:10px}.phone .card{padding:14px;margin-bottom:0}.phone .big.s{font-size:1.7em}
.phd{display:flex;justify-content:space-between;align-items:center;font-size:1.1em}.wchip{background:var(--accent);color:#fff;border-radius:calc(var(--radius) * .7);padding:12px 14px}
.pbar{display:flex;justify-content:space-around;align-items:center;padding:8px 6px 12px;background:var(--surface);border-top:1px solid var(--line)}
.pn2{text-align:center;font-size:.72em;color:var(--muted);line-height:1.3}.pn2.on{color:var(--accent);font-weight:700}.pn2.fab{width:46px;height:46px;border-radius:50%;background:var(--accent);color:#fff;font-size:1.8em;display:grid;place-items:center;line-height:1}
.phone .cl li{grid-template-columns:1fr auto;gap:2px 8px}.phone .cl .cd{grid-column:1/3}.phone .cl .chip{display:none}
.pf{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--line)}.pf label{color:var(--muted)}.pf.need b{color:var(--bad)}.pdoc .paper{padding:12px}
.phone .askbox{padding:10px 12px;flex:none}.phone .stickyask .askbox{flex:1}.bulk .btn.pri{background:#fff;color:var(--ink);border-color:#fff}.pdoc .paper{height:auto!important;gap:7px}.pdoc .paper i:nth-child(n+6){display:none}.pdoc .paper-t{margin-top:4px}
"""

OPT_CSS = {
"A": """
.A{--z:1}
.A .topnav{display:flex;align-items:center;gap:18px;padding:16px 20px}
.A .pills{display:flex;gap:6px;background:var(--surface);border-radius:99px;padding:5px;flex:1;justify-content:center}
.A .pill{padding:8px 15px;border-radius:99px;color:var(--muted);white-space:nowrap}.A .pill.on{background:var(--ink);color:#fff}.A .pill.on em{background:#fff;color:var(--ink)}
.A .tools{display:flex;gap:8px;align-items:center}.A .subbar{margin-bottom:var(--gap);display:flex}
.A .tint1{background:var(--t1)}.A .tint2{background:var(--t2)}.A .wcard{background:var(--accent2);color:#fff}.A .wcard .sub{color:#fff}.A .wcard .wl span{border-color:rgba(255,255,255,.6)}.A .wcard .btn{border-color:#fff;background:#fff;color:var(--ink)}
.A .hero{padding:30px}.A .hero .askbox{border:0}
.A .tab{border-radius:99px}.A .tab.on{background:var(--surface)}
.A .card{box-shadow:none}
.A .rk-t{height:16px}.A .tbl th{border-bottom:0}.A .tbl tr td{border-bottom:1px solid var(--line)}
.A .bulk{border-radius:99px}.A .phone{--bg:#FAF3EA}
.A .g2a{display:grid;grid-template-columns:1.75fr 1fr;gap:var(--gap)}
.A .mm{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.A .mmh{display:flex;align-items:baseline;gap:10px}.A .mmt{font-weight:600;font-size:1.05em}
.A .mmb{display:flex;align-items:center;gap:12px;margin:4px 0 8px;flex-wrap:wrap}
.A .cmp{font-size:.9em}.A .cmp-r{display:grid;grid-template-columns:32px 1fr 56px 74px;gap:8px;align-items:center;padding:7px 0;border-top:1px solid var(--line)}
.A .cmp-h{border-top:0;color:var(--muted);font-size:.78em;padding:2px 0}.A .cmp-h span:nth-child(n+3){text-align:right}
.A .cmp-r span:nth-child(n+3){text-align:right}.A .cmp-r.now{font-weight:600}.A .mut{color:var(--muted)}
.A .hb{display:block}
.A .avail{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:16px;padding:14px 18px;background:var(--t2);border-radius:calc(var(--radius) * .75)}
.A .avail-r{text-align:right;display:flex;flex-direction:column;gap:6px;align-items:flex-end}
.A .wrow2{display:grid;grid-template-columns:repeat(4,1fr);gap:var(--gap)}
.A .wi{border-radius:calc(var(--radius) * .75);padding:16px;display:flex;flex-direction:column;gap:6px;align-items:flex-start}.A .wi.t1{background:var(--t1)}.A .wi.t2{background:var(--t2)}
.A .wi-a{font-weight:600}.A .wi-d{flex:1;min-height:34px}
.A .wstrip{display:flex;align-items:center;gap:18px}.A .wsh{display:flex;gap:8px;align-items:center;padding-right:6px}
.A .ws{display:flex;flex-direction:column;gap:2px;align-items:flex-start;background:var(--surface);border-radius:calc(var(--radius) * .6);padding:10px 14px;flex:1}.A .ws .btn{margin-top:6px}
.A .wsm{font-size:.88em;color:var(--muted);line-height:1.5}
.A .wi{flex-direction:row;align-items:center;justify-content:space-between;gap:10px;padding:14px 16px}
.A .wi .btn{padding:4px 14px;font-size:.84em;margin:0;border-radius:99px}
.A .wi-a{font-weight:600;margin-bottom:2px}
.A .lg{display:flex;gap:18px;flex-wrap:wrap;font-size:.8em;color:var(--muted);margin:2px 0 4px}.A .lg span{display:flex;align-items:center;gap:6px}
.A .lq{display:inline-block;width:12px;height:12px;border-radius:4px;border:1.5px solid var(--muted);background:transparent}.A .lq.on{background:var(--muted)}
.A .mtd .rk{grid-template-columns:90px 1fr 128px}.A .mtd .rk-t{position:relative;background:transparent;overflow:visible}
.A .mtd .rk-t u{position:absolute;left:0;top:0;height:100%;background:#FBEADD;border-radius:0 99px 99px 0}
.A .mtd .rk-t i{position:absolute;left:0;top:0;height:100%;background:var(--accent);border-radius:0 99px 99px 0}
.A .mtd .rk-v{text-align:right;color:var(--ink)}.A .mtd .rk-v .mut{font-size:.85em}
.A .phone .mtd .rk{grid-template-columns:64px 1fr 100px}
.A .mm{align-items:start}.A .chart.bottle{margin-top:4px}
""",
"B": """
.B{display:flex}
.B .side{width:214px;background:var(--surface);padding:18px 12px;display:flex;flex-direction:column;gap:2px;min-height:760px;flex:none;border-right:1px solid var(--line)}
.B .logo{padding:4px 10px 18px}.B .sbl{font-size:.72em;color:var(--muted);padding:6px 10px;letter-spacing:.08em}
.B .sbi{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:10px;color:var(--muted)}.B .sbi.on{background:var(--t1);color:var(--accent);font-weight:600}.B .ic{width:18px;text-align:center}
.B .alertrow{color:var(--bad);font-size:.88em}.B .mainw{flex:1;min-width:0}
.B .mtop{display:flex;gap:10px;align-items:center;padding:14px var(--pad) 0}
.B .tab.on{border-bottom:2px solid var(--accent)}.B .tabs{border-bottom:1px solid var(--line);gap:12px}
.B .dark{background:var(--accent);color:#fff}.B .dark .sub{color:#fff}.B .dark .wl span{border-color:rgba(255,255,255,.6)}.B .dark .btn{background:#fff;border-color:#fff;color:var(--ink)}
.B .gB{display:grid;grid-template-columns:1.35fr 1fr;gap:var(--gap)}.B .col{display:flex;flex-direction:column}
.B .homeB .card.hero{padding:6px}.B .kp .big.s{font-size:1.6em}
.B .tbl th{background:var(--t2)}.B .phone{--bg:#F2F4F1}
""",
"C": """
.C .topbar{display:flex;align-items:center;gap:16px;padding:10px 14px;background:var(--surface);border-bottom:1px solid var(--line)}
.C .topbar .askbox{max-width:460px;margin:0 auto 0 20px;padding:6px 10px}.C .tools{display:flex;gap:8px;align-items:center}
.C .ttabs{display:flex;gap:2px;background:var(--surface);border-bottom:1px solid var(--line);padding:0 12px}
.C .ttab{padding:10px 14px;color:var(--muted);border-bottom:2px solid transparent}.C .ttab.on{color:var(--ink);font-weight:600;border-bottom-color:var(--accent)}
.C .card{border:1px solid var(--line)}.C .card-h{font-size:.95em}.C .big{font-size:2em}.C .big.s{font-size:1.6em}
.C .kstrip{display:grid;grid-template-columns:repeat(6,1fr);gap:0;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:var(--gap)}
.C .kc{padding:12px 14px;border-right:1px solid var(--line)}.C .kc:last-child{border:0}.C .kv{font-size:1.5em;font-weight:600}.C .kd{font-size:.82em;color:var(--muted)}
.C .homeC .card.hero{padding:8px}.C .askbox.big{padding:12px 14px;font-size:1.05em}
.C .tbl th{background:var(--t2);text-transform:none;font-size:.9em}.C .tbl td{padding:7px 10px}
.C .tab{border-bottom:2px solid transparent}.C .tab.on{border-bottom-color:var(--accent)}
.C .btn.pri{background:var(--accent)}.C .bulk{background:var(--accent)}.C .bulk .btn{border:0}
.C .ranked .rk{margin:6px 0}.C .rk-t{height:8px;border-radius:2px}.C .rk-t i{border-radius:2px;background:var(--accent)}
.C .phone{--bg:#F6F7F9}
""",
}

SCREENS = [
 ("home", "1 · Home", s_home, "Ask first, then money and coming up, then the queue as a full-width row of four cards with what each holds (Pedro’s order: Ask > Money > Coming up > Waiting). Earnings and spending are separate, each compared with the two previous months by the same date and for the full month; the available balance sits below. Sample figures."),
 ("ask", "2 · Ask: answer and act", s_ask, "One sentence, one matching visual, sources, Open the page / Save to wiki. Three tiers shown: an undoable action with Undo; a confirm card that lists exactly what it will file; an attached photo that lands in the Inbox as a suggestion. No suggested-question chips."),
 ("money", "3 · Money", s_money, "Headline row (spent vs budget, cash with “matches the bank”, due soon, loans), breakdown and 12-month trend; tabs for the rest. The link to category history sits under the chart."),
 ("history", "4 · Category history", s_history, "“Show me the history of [category] over the last [x] months”: pick any tree level and a number of months; chart, budget line, month table, drill to the lines."),
 ("waiting", "5 · Waiting for you", s_waiting, "One queue, one count, grouped by area with filters. The 1,430 merchants have their own line outside the count."),
 ("review", "6 · Review a paper (desk)", s_review, "The paper beside what the Hub read; the missing field is marked; keyboard shortcuts; position in the queue. The suggestion says why."),
 ("merchants", "7 · Merchants in bulk", s_merchants, "Best guess filled in, select many, confirm once. Each confirmation writes one standing rule."),
 ("health", "8 · Health: people", s_health, "Name tiles, no faces, each with next follow-up and latest paper; the family’s follow-ups below; area and topic are only for browsing."),
 ("person", "9 · Health: a person", s_person, "Conditions, latest results (two labs on one line with the reference range), timeline. Sample data, not real records."),
 ("thing", "10 · A thing: a car", s_thing, "A thing gathers papers, dates, insurance and costs across categories. Same page for the house, a property, a pet, a policy or a loan. Sample data."),
 ("coming", "11 · Coming up", s_coming, "All dates across categories, including ID expiries and renewals; To-Dos are a tab here; category chips and the Who chip filter."),
 ("settings", "12 · Settings and Problems", s_settings, "Behind the gear. A problem reads what / why / what to do, with its fix button. Privacy, jobs, bank connections and the wiki live here."),
]

def render(o):
    t = TOK[o]
    root = ":root{" + "".join(f"--{k}:{v};" for k, v in [("bg", t["bg"]), ("surface", t["surface"]), ("t1", t["t1"]), ("t2", t["t2"]), ("ink", t["ink"]),
            ("muted", t["muted"]), ("line", t["line"]), ("accent", t["accent"]), ("accent2", t["accent2"]), ("bar", t["bar"]), ("radius", t["radius"]),
            ("fs", t["fs"]), ("pad", t["pad"]), ("gap", t["gap"]), ("good", t["good"]), ("bad", t["bad"])]) + "}"
    defs = (f'<svg width="0" height="0" style="position:absolute"><defs>'
            f'<pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" fill="{t["surface"]}"/><line x1="0" y1="0" x2="0" y2="7" stroke="{t["bar"]}" stroke-width="2.4"/></pattern>'
            f'<pattern id="hatchlight" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="{t["bar"]}" stroke-width="1.4" opacity=".55"/></pattern>'
            f'<pattern id="hatchg" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" fill="{t["surface"]}"/><line x1="0" y1="0" x2="0" y2="7" stroke="{t["accent2"]}" stroke-width="2.4" opacity=".55"/></pattern>'
            f'</defs></svg>')
    sw = "".join(f'<i style="background:{t[k]}" title="{k}"></i>' for k in ["bg", "surface", "t1", "t2", "ink", "accent", "accent2", "bar"])
    idx = "".join(f'<a href="#{k}">{lab}</a>' for k, lab, _, _ in SCREENS) + '<a href="#phones">Phone: Home, Ask, Approve</a>'
    frames = ""
    for k, lab, fn, note in SCREENS:
        frames += f'<div class="cap" id="{k}">{lab}<span>Option {o}</span></div><div class="frame-w" style="{root[6:-1]}">{shell(o, k, fn(o), lab)}</div><div class="note">{note}</div>'
    phones = (f'<div class="cap" id="phones">Phone<span>Option {o}: the same design on a phone. Home, Ask with a confirm card, and the light approve card for a paper uploaded on the run.</span></div>'
              f'<div class="phones" style="{root[6:-1]}">'
              f'<div class="phone-w">{p_home(o)}</div><div class="phone-w">{p_ask(o)}</div><div class="phone-w">{p_approve(o)}</div></div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hub UI, option {o}: {t['name']}</title><style>{root}{BASE_CSS}{FRAME_CSS}{OPT_CSS[o]}</style></head><body>{defs}
<div class="sheet-h"><div style="font-size:13px;color:#666"><a href="index.html">← all options</a> · mock-up, sample data, light theme, no gradients, no faces</div>
<h1>Option {o}: {t['name']}</h1><p>{PITCH[o]}</p><div class="sw">{sw}</div><div class="idx">{idx}</div></div>
<div class="sheet-m">{frames}{phones}</div>
<script>function fit(){{var z=Math.min(1,(window.innerWidth-48)/1180);document.documentElement.style.setProperty('--z',z);}}fit();addEventListener('resize',fit);</script>
</body></html>"""

INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hub UI options</title>
<style>body{font-family:system-ui,sans-serif;background:#E7E7EA;color:#222;margin:0}.w{max-width:980px;margin:0 auto;padding:32px 24px}h1{font-size:26px;margin:0 0 6px}
.g{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:22px 0}.c{background:#fff;border-radius:12px;padding:18px;text-decoration:none;color:#222;display:block}
.c h2{margin:0 0 6px;font-size:18px}.c p{font-size:14px;line-height:1.45;color:#444}.sw{display:flex;gap:4px;margin:10px 0}.sw i{width:26px;height:26px;border-radius:6px;border:1px solid rgba(0,0,0,.15)}
.n{font-size:14px;line-height:1.5;color:#333;max-width:760px}@media(max-width:800px){.g{grid-template-columns:1fr}}</style></head><body><div class="w">
<h1>Home Hub: three UI options</h1><p class="n">Wireframe mock-ups, 12 desktop screens and 3 phone screens per option, sample data. Same decisions underneath (see <code>../ux-workflow-and-screens.md</code>):
Ask first, one Waiting queue, person-first Health, things as pages, people as a filter, settings behind a gear. No gradients, no faces.</p>
<div class="g">%s</div><p class="n">Also: <a href="chart-rules.html">chart rules, how the scale adapts</a> (the same chart on nine different datasets).</p></div></body></html>"""


def render_gallery():
    tk = TOK["A"]
    root = ":root{" + "".join(f"--{k}:{v};" for k, v in [("bg", tk["bg"]), ("surface", tk["surface"]), ("t1", tk["t1"]), ("t2", tk["t2"]), ("ink", tk["ink"]),
            ("muted", tk["muted"]), ("line", tk["line"]), ("accent", tk["accent"]), ("accent2", tk["accent2"]), ("bar", tk["bar"]), ("radius", tk["radius"]),
            ("fs", tk["fs"]), ("pad", tk["pad"]), ("gap", tk["gap"]), ("good", tk["good"]), ("bad", tk["bad"])]) + "}"
    d = lambda *v: [(x, None, y, i == len(v) - 1) for i, (x, y) in enumerate(v)]
    g = lambda v: f"{v:g}"
    def cap(vals, fmt):
        lo, hi, ticks = make_scale(vals)
        return f"values {fmt(min(vals))} to {fmt(max(vals))} · axis from {fmt(lo) if lo else 'zero'} to {fmt(hi)}"
    labs = [
        ("Hemoglobin, g/dL", "two-sided range", d(("2023-11", 12.1), ("2024-11", 12.4), ("2025-03", 12.8), ("2025-11", 12.6), ("2026-03", 12.9)), g, (11.5, 15.5)),
        ("LDL cholesterol, mg/dL", "upper limit only; two values above it", d(("2024-03", 118), ("2024-11", 127), ("2025-04", 142), ("2025-11", 136), ("2026-03", 121)), g, (None, 130)),
        ("Vitamin D, ng/mL", "lower limit only; two values below it", d(("2024-03", 18), ("2024-11", 22), ("2025-04", 27), ("2025-11", 31), ("2026-03", 29)), g, (30, None)),
        ("TSH, mIU/L", "small numbers, wide range", d(("2024-03", 1.9), ("2024-11", 2.4), ("2025-04", 3.8), ("2025-11", 2.6), ("2026-03", 2.2)), g, (0.4, 4.0)),
        ("Fasting glucose, mg/dL", "one value just above the range", d(("2024-03", 88), ("2024-11", 92), ("2025-04", 95), ("2025-11", 104), ("2026-03", 99)), g, (70, 99)),
        ("Platelets, ×10³/µL", "big numbers, range far away", d(("2024-03", 250), ("2024-11", 262), ("2025-04", 241), ("2025-11", 275), ("2026-03", 268)), g, (150, 400)),
    ]
    mon = [
        ("Coffee and snacks, € per month", "full month and same-day fill; values close together", [("Jun", 36, 30, False), ("Jul", 41, 33, False), ("Aug", 44, 37, False), ("Sep", 39, 31, False), ("Oct", 42, 35, False), ("Nov", None, 34, True)], _eur),
        ("Electricity, kWh per month", "same rule, other unit", [("Jun", 250, 198, False), ("Jul", 270, 214, False), ("Aug", 290, 226, False), ("Sep", 260, 205, False), ("Oct", 285, 230, False), ("Nov", None, 214, True)], lambda v: f"{v:,}"),
        ("Leisure, € per month", "values reach zero: the axis starts at zero", [("Jun", 150, 120, False), ("Jul", 0, 0, False), ("Aug", 60, 48, False), ("Sep", 0, 0, False), ("Oct", 150, 130, False), ("Nov", None, 124, True)], _eur),
    ]
    s1 = ""
    for title, sub, items, fmt, ref in labs:
        s1 += card(f'<div class="sub">{sub}</div>' + col_chart("e", items, 330, 200, fmt=fmt, ref=ref) + f'<div class="sub gcap">{cap(_vals(items), fmt)}</div>', "", title)
    for title, sub, items, fmt in mon:
        s1 += card(f'<div class="sub">{sub}</div>' + col_chart("s", items, 330, 200, fmt=fmt) + f'<div class="sub gcap">{cap(_vals(items), fmt)}</div>', "", title)
    gl = d(("2024-03", 88), ("2024-11", 92), ("2025-04", 95), ("2025-11", 104), ("2026-03", 99))
    ldl = d(("2024-03", 118), ("2024-11", 127), ("2025-04", 142), ("2025-11", 136), ("2026-03", 121))
    sm = [("Continente", 212, 230), ("Pingo Doce", 168, 190), ("Lidl", 131, 125), ("Mercadona", 118, 120)]
    def hcap(rows):
        vs = [v for _, a, b in rows for v in (a, b)]; lo, hi, _t = make_scale(vs)
        return f"values {_eur(min(vs))} to {_eur(max(vs))} · axis from {_eur(lo) if lo else 'zero'} to {_eur(hi)}"
    s2 = (card('<div class="sub">strong line: spent by the same day of each month · pale line: the full month · no forecast</div>' + line_chart("s", GROC_ITEMS, 560, 200) + f'<div class="sub gcap">{cap(_vals(GROC_ITEMS), _eur)}</div>', "", "History line: Groceries, 12 months")
          + card('<div class="sub">dashed: straight-line fit through the strong points, only over the data, never beyond it</div>' + line_chart("s", SPEND_ITEMS, 560, 200, trend=True) + f'<div class="sub gcap">{cap(_vals(SPEND_ITEMS), _eur)}</div>', "", "Trendline: spending, 12 months")
          + card('<div class="sub">tinted track: last month in full · solid: this month so far</div>' + hbar_chart(GROUPS, 520, label_w=80, right_w=130) + f'<div class="sub gcap">{hcap([(a, b, c) for a, b, c in GROUPS])}</div>', "", "Horizontal bars: spending by group")
          + card('<div class="sub">values close together: the cap on exaggeration keeps differences honest</div>' + hbar_chart(sm, 520, label_w=80, right_w=130) + f'<div class="sub gcap">{hcap(sm)}</div>', "", "Horizontal bars: supermarkets")
          + card('<div class="sub">a lab value as a line, reference range as a band, a value outside it in red</div>' + line_chart("e", gl, 560, 200, fmt=g, ref=(70, 99), subs=["Cintramédica", "Cintramédica", "HSM", "HSM", "HSM"]) + f'<div class="sub gcap">{cap(_vals(gl), g)}</div>', "", "Line of a lab value: fasting glucose, mg/dL")
          + card('<div class="sub">upper limit only</div>' + line_chart("e", ldl, 560, 200, fmt=g, ref=(None, 130), trend=True, per="per result") + f'<div class="sub gcap">{cap(_vals(ldl), g)}</div>', "", "Lab trendline: LDL cholesterol, mg/dL"))
    intro = ('<p class="gi"><b>One rule for every chart</b>, whatever its type and whoever asks for it (a page of the Hub or a question to Ask): columns, history line, trendline, horizontal bars. '
             'Nothing is set per chart. The axis shows its real values. Its floor is worked out from the numbers: it shows the difference (40% of the spread below the smallest value) '
             'but is capped so that a visual change is never more than 1.5 times the real change (floor at most a third of the smallest value), then lowered to a tick. '
             'Numbers close together therefore sit on a near-zero axis and stay honest; numbers far apart can start higher. If the floor reaches zero, the axis starts at zero. '
             'The current month shows only what has happened so far (no forecast, no budget); past months show their full month and the same-day value. '
             'A reference range is a pale band behind the data (only the part inside the axis) and a value outside it is red. All figures are sample data.</p>')
    body = f'<h2 class="gh">Columns, nine different datasets</h2><div class="g3 gg">{s1}</div><h2 class="gh">The other chart types, same rule</h2><div class="g2 gg2">{s2}</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hub chart rules: one rule for every chart</title><style>{root}{BASE_CSS}{FRAME_CSS}{OPT_CSS["A"]}.gi{{max-width:900px;line-height:1.5;margin-bottom:18px}}.gg{{grid-template-columns:repeat(3,1fr)}}.gg2{{grid-template-columns:1fr 1fr}}.gcap{{margin-top:6px}}.gh{{font-size:1.2em;margin:22px 0 12px}}</style></head><body>
<div class="sheet-h"><div style="font-size:13px;color:#666"><a href="index.html">← all options</a></div><h1>Chart rules: one rule for every chart</h1></div>
<div class="sheet-m"><div class="frame A" style="min-height:0"><main class="content">{intro}{body}</main></div></div>
<script>function fit(){{var z=Math.min(1,(window.innerWidth-48)/1180);document.documentElement.style.setProperty('--z',z);}}fit();addEventListener('resize',fit);</script></body></html>"""

os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, 'chart-rules.html'), 'w').write(render_gallery())
cards = ""
for o in "ABC":
    open(os.path.join(OUT, f"option-{o.lower()}.html"), "w").write(render(o))
    t = TOK[o]
    sw = "".join(f'<i style="background:{t[k]}"></i>' for k in ["bg", "t1", "ink", "accent", "accent2"])
    cards += f'<a class="c" href="option-{o.lower()}.html"><h2>Option {o}: {t["name"]}</h2><div class="sw">{sw}</div><p>{PITCH[o]}</p></a>'
open(os.path.join(OUT, "index.html"), "w").write(INDEX % cards)
print("built", os.listdir(OUT))

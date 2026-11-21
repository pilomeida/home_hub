"""The answer card's one visual (PLAN step 10g, slice 2): a number, a chart, a table, or the paper the answer rests
on. The spec comes from the tool that read the data (ToolOutput.visual), never from the AI's text, and every chart
goes through the one chart component on its scale rule (app/ui/charts.py)."""

from __future__ import annotations

import json
from html import escape
from typing import Optional

from markupsafe import Markup

from app.ui import charts


def render(visual_json: Optional[str], citations: list) -> Markup:
    spec = json.loads(visual_json) if visual_json else None
    if spec is None:
        return _paper(citations)
    kind = spec.get("kind")
    if kind == "number":
        return Markup(f'<div class="ask-visual"><div class="big s">{escape(spec["value"])}</div>'
                      f'<div class="sub">{escape(spec.get("label") or "")}</div></div>')
    if kind == "columns":
        items = [charts.Item(i["label"], i["value"], full=i.get("full"), current=i.get("current", False))
                 for i in spec["items"]]
        fmt = charts.plain if spec.get("fmt") == "plain" else charts.euros
        ref = tuple(spec["ref"]) if spec.get("ref") else None
        return Markup('<div class="ask-visual">') + charts.columns(
            items, kind=spec.get("colour", "s"), fmt=fmt, w=min(120 + 70 * len(items), 760), h=190, ref=ref,
            title=spec.get("title", "")) + Markup("</div>")
    if kind == "table":
        head = "".join(f"<th>{escape(h)}</th>" for h in spec["headers"])
        body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in spec["rows"])
        return Markup(f'<div class="ask-visual"><table class="tbl mini"><thead><tr>{head}</tr></thead>'
                      f"<tbody>{body}</tbody></table></div>")
    return Markup("")


def _paper(citations: list) -> Markup:
    """No chart or table: the first paper the answer cites, as a card that opens it."""
    paper = next((c for c in citations if c.get("ref", "").startswith(("doc:", "rec:"))), None)
    if paper is None:
        return Markup("")
    return Markup(f'<a class="ask-visual ask-paper" href="{escape(paper["url"])}"><span class="file">▤</span>'
                  f'<span>{escape(paper["label"])}</span><span class="sub">Open ›</span></a>')

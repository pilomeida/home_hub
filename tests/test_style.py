"""PLAN step 11b: one look across every page, written down in docs/STYLE.md and kept by hub.css's tokens."""

import re
from pathlib import Path

CSS = Path("app/static/hub.css").read_text()


def test_the_style_guide_exists_and_is_referenced():
    guide = Path("docs/STYLE.md").read_text()
    assert "Update this file when a component changes" in guide.splitlines()[2]
    for section in ("## 1. Tokens", "## 2. Layout and width", "## 3. Components", "Card", "Stat tile", "Table",
                    "Form field", "Buttons", "Tag / chip", "Messages", "Empty state"):
        assert section in guide, section
    assert "docs/STYLE.md" in Path("CLAUDE.md").read_text()


def test_the_stylesheet_is_light_only_and_sized_in_rem():
    assert "prefers-color-scheme" not in CSS                       # dark rows on a light page are gone
    assert re.findall(r"font-size:\s*\d+px", CSS) == []            # every size follows the type scale
    assert "text-transform: uppercase" not in CSS                  # sentence case everywhere


def test_plain_elements_look_like_home(client):
    for rule in ("button, input[type=submit]", "main table:not([class])", "select, textarea", "button.pri"):
        assert rule in CSS, rule
    html = client.get("/overview").text
    assert "HOME &amp; FAMILY" not in html and '<div class="fc-brand">Money</div>' in html

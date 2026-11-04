"""The model's words in the code (work-list B1b): a paper's kind is `.paper_kind` (slice 2), its category `.category`
(slice 3c, was `.domain`). Jinja shows a missing attribute as nothing, so a template still asking for an old
name would go blank without an error, and SQLModel drops an unknown keyword: these tests catch both."""

import re
from pathlib import Path

import pytest

# What a template may still read under the old names: the form's errors (keyed by the HTML field names "category" and
# "domain") and a spending group's name.
ALLOWED = {"errors.category", "errors.domain", "row.category"}
OLD_NAMES = re.compile(r"[\w.\]\)]+\.(?:category|domain)\b")


def test_no_template_reads_a_paper_kind_or_category_by_an_old_name():
    found = set()
    for p in Path("app/templates").rglob("*.html"):
        text = re.sub(r"<style.*?</style>", "", p.read_text(), flags=re.S)       # CSS class names are not attributes
        for m in OLD_NAMES.finditer(text):
            name = m.group(0)
            if name in ALLOWED or name.endswith(".category") and _reads_the_category(name):
                continue
            found.add(f"{p}: {name}")
    assert not found, f"use .paper_kind for a paper's kind and .category for its category: {sorted(found)}"


def _reads_the_category(name: str) -> bool:
    """`.category` is right where the object is a paper, record, to-do, spec or privacy type (its category)."""
    return name.split(".")[0] in {"document", "record", "todo", "spec", "selected_spec", "r", "item", "entry", "c"}


@pytest.mark.parametrize("old", ["domain"])
def test_a_paper_made_with_an_old_name_is_refused_not_dropped(old):
    from app.models.category import Category
    from app.models.document import Document, DocumentSource
    with pytest.raises(TypeError, match=f"no field {old}"):
        Document(filename="a.pdf", file_path="/x", content_hash="h", source=DocumentSource.MANUAL, **{old: Category.FINANCIALS})
    made = Document(filename="a.pdf", file_path="/x", content_hash="h", source=DocumentSource.MANUAL,
                    category=Category.FINANCIALS, paper_kind="bill")
    assert (made.category, made.paper_kind) == (Category.FINANCIALS, "bill")


def test_no_template_calls_a_helper_by_its_old_name():
    """Calling an undefined helper fails on the page, but only when the page is opened: catch it here."""
    old = re.compile(r"\b(?:domain_label|domain_nav|category_label\(item\.paper_kind)\b")
    found = [f"{p}: {m.group(0)}" for p in Path("app/templates").rglob("*.html") for m in old.finditer(p.read_text())]
    assert not found, f"use category_label / category_nav (and kind_label for a kind): {found}"

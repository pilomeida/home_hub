"""The paper kind's attribute is `paper_kind` (work-list B1b, slice 2). Jinja shows a missing attribute as nothing,
so a template still asking for `.category` on a paper would go blank without an error: this test catches it."""

import re
from pathlib import Path

# The only `.category` a template may read: the form's error for its "category" field, and a spending group's name.
ALLOWED = {"errors.category", "row.category"}


def test_no_template_reads_a_paper_kind_by_its_old_name():
    found = {f"{p}: {m.group(0)}" for p in Path("app/templates").rglob("*.html")
             for m in re.finditer(r"[\w.\]\)]+\.category\b", p.read_text()) if m.group(0) not in ALLOWED}
    assert not found, f"use .paper_kind: {sorted(found)}"


def test_a_paper_made_with_the_old_name_is_refused_not_kindless():
    import pytest
    from app.models.document import Document, DocumentSource
    with pytest.raises(TypeError, match="no field category"):
        Document(filename="a.pdf", file_path="/x", content_hash="h", source=DocumentSource.MANUAL, category="bill")

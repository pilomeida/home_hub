"""Step 9 D2: the places in the spending tree the code relies on are named once (app/services/tree_slugs.py) and
exist in the tree the seed builds, so renaming a node in the seed fails here instead of breaking filing silently."""

import ast
from pathlib import Path

from sqlmodel import select

from app.models.category_node import CategoryNode
from app.services import tree_slugs
from app.services.taxonomy import ensure_taxonomy

APP = Path(__file__).resolve().parent.parent / "app"
# Seed and history data that only lists slugs; payment entities move into rules in step 9 D8.
_DATA_FILES = {"services/tree_slugs.py", "services/taxonomy_slugmap.py", "services/tag_seed.py",
               "services/payment_entities.py"}


def _tree(session) -> set[str]:
    ensure_taxonomy(session)
    return {n.slug for n in session.exec(select(CategoryNode)).all()}


def test_every_named_place_exists_in_the_seeded_tree(session):
    assert sorted(tree_slugs.ALL - _tree(session)) == []


def test_no_other_code_writes_a_slug_with_a_dot(session):
    slugs = {s for s in _tree(session) if "." in s}
    found = []
    for path in sorted(APP.rglob("*.py")):
        rel = str(path.relative_to(APP))
        if rel in _DATA_FILES:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in slugs:
                found.append(f"{rel}:{node.lineno} {node.value}")
    assert found == []

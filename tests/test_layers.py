"""Step 9 D2: a router never imports another router, and nothing below the routers imports one. A page piece more
than one page shows lives in app/ui; logic lives in app/services. Every import is checked, including those
inside a function."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def _imports(path: Path) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
            if node.module == "app.routers":
                found.extend((node.lineno, f"app.routers.{a.name}") for a in node.names)
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, a.name) for a in node.names)
    return found


def _offenders(folder: str) -> list[str]:
    return [f"{p.relative_to(APP.parent)}:{line} imports {module}"
            for p in sorted((APP / folder).rglob("*.py"))
            for line, module in _imports(p) if module.startswith("app.routers")]


def test_no_router_imports_a_router():
    assert _offenders("routers") == []


def test_nothing_below_the_routers_imports_one():
    for folder in ("services", "ui", "categories", "jobs", "privacy", "models"):
        assert _offenders(folder) == [], folder

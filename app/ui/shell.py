"""The shell (PLAN step 10b): the menu, the section's own pages, the gear and the phone bar, from one list.

Menu (docs/design/ux-workflow-and-screens.md): Home · Money · Health · Things · Papers · Coming up · Waiting for you.
Every page the old side menu reached is in one section's row or behind the gear, so nothing becomes unreachable
(tested), and every old URL keeps working (no route changes here). A section's pages come from the category registry
where a category already lists them, so a new category's pages appear without editing this file."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass
class Section:
    key: str
    label: str
    url: str
    prefixes: tuple[str, ...]
    links: list[Link] = field(default_factory=list)


def _category_links(*categories: str) -> list[Link]:
    from app.categories.registry import implemented_categories
    out: list[Link] = []
    for spec in implemented_categories():
        if spec.category.value in categories:
            out += [Link(l.label, l.url) for l in spec.nav_links]
    return out


# Behind the gear: how the Hub runs, as today (Privacy, Scheduled jobs, Bank connections, What the Hub knows).
SETTINGS = [Link("Privacy", "/privacy"), Link("Scheduled jobs", "/schedules"), Link("Bank connections", "/financials/bank/"),
            Link("What the Hub knows", "/wiki"), Link("Wiki check", "/wiki/lint"), Link("Problems", "/problems")]
OTHER_APPS = [Link("Recipes", "https://recipes.cdafamily.casa"), Link("Exercise", "https://gym.cdafamily.casa")]


_PLACED = ("financials", "health", "house", "insurance", "identity", "vehicles", "education", "pets")     # the built-in areas a section names below


def _unplaced_links() -> list[Link]:
    """Pages of an area no section names: one Pedro added in the Inbox (step 12a) shows in Papers."""
    from app.categories.registry import implemented_categories
    return [Link(l.label, l.url) for spec in implemented_categories() if spec.category.value not in _PLACED
            for l in spec.nav_links]


# Money's tabs (step 10d), in the mock-up's order: the Money page, the money category's own pages under the tab names
# the design gives them (only those the registry lists), then the category history.
MONEY_NAMES = {"/financials/bills": "Bills & due", "/financials/loans": "Loans",
               "/financials/utilities/electricity": "Utilities", "/financials/transactions": "All lines"}


def _money_tabs() -> list[Link]:
    listed = [l for l in _category_links("financials") if l.url not in ("/financials/coming-up", "/financials/bank/")]
    urls = {l.url for l in listed}
    named = [Link(label, url) for url, label in MONEY_NAMES.items() if url in urls]
    return ([Link("Spending", "/money")] + named + [l for l in listed if l.url not in MONEY_NAMES]   # a new page shows
            + [Link("Category history", "/money/history")])


def sections() -> list[Section]:
    money = _money_tabs()
    return [
        Section("home", "Home", "/", ("/", "/ask", "/inbox", "/overview"),
                [Link("Home", "/"), Link("Overview", "/overview"), Link("Ask", "/ask"), Link("Inbox", "/inbox")]),
        Section("money", "Money", "/money", ("/money", "/financials", "/budget", "/to-check"), money),
        Section("health", "Health", "/health/family", ("/health",), [Link("Family", "/health/family")] + _category_links("health")),
        Section("things", "Things", "/house", ("/house", "/insurance", "/vehicles", "/pets"),
                _category_links("house", "insurance", "vehicles", "pets")),
        Section("papers", "Papers", "/papers", ("/papers", "/search", "/areas", "/identity", "/education", "/documents"),
                [Link("All papers", "/papers"), Link("Search", "/search")] + _category_links("identity", "education")
                + _unplaced_links()),
        Section("coming", "Coming up", "/financials/coming-up", ("/financials/coming-up", "/todos"),
                [Link("Dates", "/financials/coming-up"), Link("To-Dos", "/todos")]),
        Section("waiting", "Waiting for you", "/waiting", ("/waiting",), [Link("Waiting for you", "/waiting")]),
    ]


def active(path: str, all_sections: Optional[list[Section]] = None) -> Optional[Section]:
    """The section a page belongs to: the longest prefix that matches ("/financials/coming-up" is Coming up, not
    Money). None for the gear's pages."""
    best, size = None, -1
    for s in all_sections or sections():
        for p in s.prefixes:
            hit = path == p if p == "/" else (path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p + "?"))
            if hit and len(p) > size:
                best, size = s, len(p)
    return best


def every_link() -> list[Link]:
    """Every page the shell reaches (the sections' rows, the gear, other apps): what the "More" page lists."""
    out: list[Link] = []
    for s in sections():
        out += [Link(s.label, s.url)] + s.links
    return out + SETTINGS + OTHER_APPS


__all__ = ["Link", "Section", "SETTINGS", "OTHER_APPS", "sections", "active", "every_link"]

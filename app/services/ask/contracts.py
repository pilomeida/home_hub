"""The contract between the Ask engine and anything that feeds it context.
Core tools (wiki, documents, todos) and domain tools declared on a
DomainSpec (spec.ask_tools) all implement AskTool, so a new domain plugs
into Ask through its registry entry without editing Ask.

PRIVACY: a tool that returns anything read from a document, record, wiki claim, transaction or to-do
MUST limit it to what `app.privacy.reach.reach_for(session)` allows (see app/privacy/reach.py); while
levels are binding, content of a type the AI may not read must never be returned. The tripwire test
(tests/test_privacy_reach.py) calls every registered tool and fails when excluded content gets out.
A tool whose output contains content of a private (High) type MUST set `tier=Tier.HIGH`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlmodel import Session

from app.privacy.tiers import Tier


@dataclass(frozen=True)
class Citable:
    """Something an answer may cite. `ref` is what Claude writes inside
    [[...]] (e.g. "wiki:12", "doc:34", "todo:5", "financials:txn:9")."""

    ref: str
    label: str
    url: str


@dataclass
class ToolOutput:
    text: str
    citables: list[Citable] = field(default_factory=list)
    content_blocks: list[dict] = field(default_factory=list)  # extra Claude blocks (document/image)
    used_raw_sources: bool = False  # True when the output came from outside the wiki
    is_error: bool = False
    tier: Optional[Tier] = None   # HIGH: the output holds private content; the engine raises the run's ratchet
    opened: str = ""              # plain words for the notice, e.g. "3 private records"


@dataclass(frozen=True)
class AskTool:
    name: str  # domain tools MUST be prefixed "<domain.value>_"
    description: str
    input_schema: dict
    run: Callable[[Session, dict], ToolOutput]

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

"""The contract between the Ask engine and anything that feeds it context.
Core tools (wiki, documents, todos) and domain tools declared on a
DomainSpec (spec.ask_tools) all implement AskTool, so a new domain plugs
into Ask through its registry entry without editing Ask."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlmodel import Session


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


@dataclass(frozen=True)
class AskTool:
    name: str  # domain tools MUST be prefixed "<domain.value>_"
    description: str
    input_schema: dict
    run: Callable[[Session, dict], ToolOutput]

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

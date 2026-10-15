"""What a scheduled job reports when it finishes (kept apart from the registry so job runners can import it)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobOutcome:
    outcome: str          # ok | partial | failed
    summary: str          # plain language: what happened, and why if it did not fully work

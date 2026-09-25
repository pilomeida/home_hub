from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from app.models.domain import Domain
from app.models.wiki_lint import LintFindingKind

DETERMINISTIC_KINDS = frozenset({
    LintFindingKind.ORPHAN_PAGE, LintFindingKind.MISSING_LINK, LintFindingKind.STALE_LINK,
    LintFindingKind.UNSOURCED_CLAIM, LintFindingKind.UNINGESTED_SOURCES, LintFindingKind.STALE_SAVED_ANSWER,
})


@dataclass(frozen=True)
class FindingDraft:
    kind: LintFindingKind
    summary: str
    domain: Optional[Domain] = None
    wiki_page_ids: tuple[int, ...] = ()   # order kept for storage (MISSING_LINK/STALE_LINK = (from, to))
    claim_ids: tuple[int, ...] = ()
    document_ids: tuple[int, ...] = ()
    record_ids: tuple[int, ...] = ()
    suggested_action: Optional[str] = None

    def fingerprint(self) -> str:
        """Identity = what it's about, not how it's worded; ids sorted so order never duplicates."""
        parts = [self.kind.value] + [",".join(map(str, sorted(ids))) for ids in
                                     (self.wiki_page_ids, self.claim_ids, self.document_ids, self.record_ids)]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

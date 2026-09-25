"""Domain registry contracts.

Every household domain (Financials, House, Health, Education, Vehicles,
Legal & Identity) is declared once as a DomainSpec. Shared code -- the
ingestion core, the nav, the '/' Overview, the to-do backlogs, the wiki
Ingest operation, and (Plan B) the Inbox classifier / (Plan C) Ask --
iterates the registry and never branches on a specific domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Optional

from sqlmodel import Session

from app.models.document import Document
from app.models.domain import Domain
from app.models.record import Record


class FieldKind(str, Enum):
    TEXT = "text"          # one-line free text
    LONGTEXT = "longtext"  # multi-line free text (notes, observations)
    DATE = "date"          # stored as ISO YYYY-MM-DD
    CHOICE = "choice"      # fixed options (static `choices` or a `choices_provider`)
    SUGGEST = "suggest"    # free text, suggested from previously used values ("dropdown + add new")


class MediaKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


DEFAULT_MEDIA = frozenset({MediaKind.PDF, MediaKind.IMAGE})

# "category" is a pseudo-field available to FactSpec.field (resolves to the
# document's category label), so no real field may use that key.
RESERVED_FIELD_KEYS = frozenset({"category"})


class SourceKind(str, Enum):
    DOCUMENT = "document"  # the entry IS an uploaded file (fields live on the Document)
    RECORD = "record"      # the entry is typed in by hand (fields live on a Record); a file may be attached


def _no_derived_fields(category: Optional[str], fields: dict[str, str]) -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class CategorySpec:
    value: str            # stored in Document.category, e.g. "warranty_invoice"
    label: str            # human label, e.g. "Warranty / invoice"
    description: str      # one sentence; also fed to Plan B's classifier prompt
    accepted_media: frozenset[MediaKind] = DEFAULT_MEDIA
    kind: SourceKind = SourceKind.DOCUMENT


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: FieldKind
    categories: Optional[frozenset[str]] = None      # None = every category of the domain
    media: Optional[frozenset[MediaKind]] = None     # None = any file type
    required: bool = False
    choices: tuple[tuple[str, str], ...] = ()        # CHOICE: static (value, label) options
    choices_provider: Optional[Callable[[Session], list[tuple[str, str]]]] = None  # CHOICE: dynamic options
    help: str = ""

    def applies_to(self, category: Optional[str], media_kind: Optional[MediaKind] = None) -> bool:
        if self.categories is not None and category not in self.categories:
            return False
        if media_kind is not None and self.media is not None and media_kind not in self.media:
            return False
        return True

    def options(self, session: Session) -> list[tuple[str, str]]:
        if self.choices_provider is not None:
            return list(self.choices_provider(session))
        return list(self.choices)


class FactPolicy(str, Enum):
    REPLACE = "replace"  # a different value supersedes the current one
    LATEST = "latest"    # keep the greatest value (ISO dates); an older value is recorded as already superseded


@dataclass(frozen=True)
class FactSpec:
    key: str                                   # claim key on the wiki page, e.g. "warranty_expires"
    label: str                                 # human label, e.g. "Warranty expires"
    field: str                                 # document field key it comes from ("category" = category label)
    categories: Optional[frozenset[str]] = None
    policy: FactPolicy = FactPolicy.REPLACE
    note_field: Optional[str] = None


@dataclass(frozen=True)
class LinkSpec:
    field: str                                  # document field naming the target entity, e.g. "room"
    target_page_type: str                       # e.g. "house.room"
    categories: Optional[frozenset[str]] = None
    bidirectional: bool = True


@dataclass(frozen=True)
class EntityTypeSpec:
    page_type: str                  # e.g. "house.item"; unique across all domains
    label: str                      # index section heading, e.g. "Items"
    key_field: str                  # document field naming the entity, e.g. "item_name"
    categories: frozenset[str]      # document categories that feed this entity
    facts: tuple[FactSpec, ...]
    links: tuple[LinkSpec, ...] = ()


@dataclass(frozen=True)
class WikiSchema:
    guidance: str = ""  # LLM guidance for free-form topic pages; "" = no LLM assessment for this domain
    entity_types: tuple[EntityTypeSpec, ...] = ()


@dataclass(frozen=True)
class NavLink:
    label: str
    url: str


@dataclass
class CardLine:
    text: str
    url: Optional[str] = None
    attention: bool = False


@dataclass
class DomainCard:
    label: str
    url: str
    lines: list[CardLine] = field(default_factory=list)
    open_todos: int = 0  # filled in by app.services.domain_overview, never by the domain


class DomainHandler:
    """Domain-specific processing of a finalized Document. `process` runs
    once, after the ingestion core has set domain/category/fields; it must
    leave the Document PROCESSED or NEEDS_ATTENTION. `on_fields_changed`
    runs after a human edits the document's fields."""

    async def process(self, session: Session, document: Document) -> Document:
        raise NotImplementedError

    async def on_fields_changed(
        self, session: Session, document: Document, previous_fields: dict[str, str]
    ) -> None:
        return None

    async def process_record(self, session: Session, record: Record) -> None:
        from app.services.wiki_engine import ingest_into_wiki  # lazy: wiki_engine imports the registry
        await ingest_into_wiki(session, record)

    async def on_record_changed(self, session: Session, record: Record, previous_fields: dict[str, str]) -> None:
        from app.models.wiki import WikiOperation
        from app.services.wiki_engine import ingest_into_wiki
        await ingest_into_wiki(session, record, operation=WikiOperation.EDIT)

    def refile_blocker(self, session: Session, document: Document) -> Optional[str]:
        """Why this document cannot leave its current classification, or None."""
        return None

    async def withdraw(self, session: Session, document: Document) -> None:
        """Reverse this domain's own derived data for a document being re-filed
        away (the core already handles wiki claims and document-linked to-dos)."""
        return None


class UnknownCategoryError(KeyError):
    pass


@dataclass(frozen=True)
class DomainSpec:
    domain: Domain
    label: str
    description: str                                  # one sentence; also fed to Plan B's classifier
    home_url: str                                     # the domain tab's landing page
    categories: tuple[CategorySpec, ...]
    fields: tuple[FieldSpec, ...]
    handler: DomainHandler
    nav_links: tuple[NavLink, ...]
    overview_card: Callable[[Session, date], DomainCard]
    document_url: Callable[[Document], str]
    wiki: WikiSchema = WikiSchema()
    infers_category: bool = False                     # True: handler may determine a missing category itself
    record_url: Optional[Callable[[Record], str]] = None
    derive_fields: Callable[[Optional[str], dict[str, str]], dict[str, str]] = _no_derived_fields
    ask_tools: tuple = ()                             # tuple[app.services.ask.contracts.AskTool, ...] (Plan C)

    def __post_init__(self) -> None:
        values = [c.value for c in self.categories]
        if len(values) != len(set(values)):
            raise ValueError(f"{self.label}: duplicate category values")
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.label}: duplicate field keys")
        reserved = RESERVED_FIELD_KEYS.intersection(keys)
        if reserved:
            raise ValueError(f"{self.label}: reserved field keys used: {sorted(reserved)}")

    def category(self, value: str) -> CategorySpec:
        for category in self.categories:
            if category.value == value:
                return category
        raise UnknownCategoryError(value)

    def category_label(self, value: Optional[str]) -> str:
        if value is None:
            return ""
        try:
            return self.category(value).label
        except UnknownCategoryError:
            return value

    def fields_for(self, category: Optional[str], media_kind: Optional[MediaKind] = None) -> list[FieldSpec]:
        return [f for f in self.fields if f.applies_to(category, media_kind)]
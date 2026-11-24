"""The shared Inbox: every document arriving through an ingestion channel
(email, Telegram, any future one) enters via receive_document() and waits,
unfinalized (domain = NULL, status PENDING_REVIEW), until a human approves
or discards it. This is first-time filing only; corrections after filing
belong to Plan A's document edit flow.

Approval is the ONLY place a channel document is finalized: it validates
the human's choice with the ingestion core's validate_classification() and
then calls finalize_document() -- the same core a manual upload uses, which
runs the domain handler and the wiki Ingest. Nothing here touches the wiki
(other than the REVIEW log line), extraction or todos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.categories.fields import InvalidClassification
from app.categories.registry import get_spec, is_implemented
from app.models.document import Document, DocumentStatus
from app.models.category import Category
from app.models.inbox_item import InboxItem, InboxSkip
from app.models.paper_type import PaperType
from app.models.wiki import WikiOperation
from app.services.category_classifier import (
    CONFIDENT_THRESHOLD,
    CategorySuggestion,
    suggest_category_and_kind,
)
from app.services.ingestion import (
    Classification,
    IncomingFile,
    finalize_document,
    receive_file,
    validate_classification,
)
from app.privacy.ratchet import ratchet_scope
from app.privacy.tiers import Tier
from app.problems import Problem, explain, record_problem
from app.services.paper_uses import refuse_if_used
from app.privacy.recognise import original_sender, recognise, remember
from app.privacy.scope import require_ready
from app.privacy.settings import TierHeld
from app.services import paper_types
from app.services.wiki_store import append_log


class InboxError(Exception):
    """The document isn't waiting in the Inbox (unknown, or already handled)."""


@dataclass
class InboxReceipt:
    document: Document
    inbox_item: Optional[InboxItem]
    duplicate: bool


# --- a paper added from a page (the + "Add a paper", PLAN step 10h; a file attached to an Ask question, step 10g) --

MAX_PAGE_UPLOAD_BYTES = 25 * 1024 * 1024

PAGE_UPLOAD_PROBLEMS = {
    "empty": Problem("Not added.", "No file was chosen, so there was nothing to put in the Inbox.",
                     "Choose a file or take a photo, then press Add."),
    "big": Problem("Not added.", "The file is bigger than 25 MB, which the Hub does not take from the page.",
                   "Send a smaller scan or photo, or email it to the Hub's address."),
    "type": Problem("Not added.", "The Hub only takes PDFs, photos and short videos, and this file is none of those.",
                    "Save it as a PDF or take a photo of it, then add it again."),
}


class UploadRefused(Exception):
    """A file from a page was not added; `key` names its message in PAGE_UPLOAD_PROBLEMS."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key
        self.problem = PAGE_UPLOAD_PROBLEMS[key]


async def add_from_page(session: Session, filename: Optional[str], content: bytes, *, uploaded_by: Optional[str],
                        note: Optional[str], classify=None) -> InboxReceipt:
    """A paper added from a page goes to the Inbox exactly as one sent by email or Telegram (same recognition, same
    reading, same approval). Raises UploadRefused before anything is stored."""
    from app.categories.fields import media_kind_for
    from app.models.document import DocumentSource
    if not filename or not content:
        raise UploadRefused("empty")
    if len(content) > MAX_PAGE_UPLOAD_BYTES:
        raise UploadRefused("big")
    if media_kind_for(filename).value == "other":
        raise UploadRefused("type")
    return await receive_document(
        session, IncomingFile(filename=filename, content=content, source=DocumentSource.MANUAL, uploaded_by=uploaded_by),
        context_text=(note or "").strip() or None, external_ref=None, classify=classify or suggest_category_and_kind)


@dataclass
class InboxEntry:
    item: InboxItem
    document: Document
    confident: bool
    suggestion_label: Optional[str]


def record_skip(session: Session, *, filename: str, source: str, reason: str, detail: str,
                document_id: Optional[int] = None, external_ref: Optional[str] = None) -> InboxSkip:
    """Keep a file that was not added findable: the Inbox page lists it with the reason. Never raises."""
    skip = InboxSkip(filename=filename, source=source, reason=reason, detail=detail, document_id=document_id,
                     external_ref=external_ref)
    try:
        session.add(skip)
        session.commit()
    except Exception:
        session.rollback()          # keeping a note must never stop the arrival being handled
    return skip


def recent_skips(session: Session, days: int = 30, limit: int = 50) -> list[tuple[InboxSkip, Optional[Document]]]:
    since = datetime.utcnow() - timedelta(days=days)
    rows = session.exec(select(InboxSkip).where(InboxSkip.created_at >= since)
                        .order_by(InboxSkip.created_at.desc()).limit(limit)).all()
    return [(s, session.get(Document, s.document_id) if s.document_id else None) for s in rows]


def item_for(session: Session, document_id: int) -> Optional[InboxItem]:
    return session.exec(select(InboxItem).where(InboxItem.document_id == document_id)).first()


async def receive_document(
    session: Session,
    incoming: IncomingFile,
    *,
    context_text: Optional[str],
    external_ref: Optional[str],
    classify=suggest_category_and_kind,
) -> InboxReceipt:
    received = receive_file(session, incoming, initial_status=DocumentStatus.PENDING_REVIEW)
    document = received.document
    if received.duplicate:
        where = ("it is still waiting in the Inbox" if document.status == DocumentStatus.PENDING_REVIEW
                 else f"it is already filed ({describe(document.category, document.paper_kind) or 'in the Hub'})")
        record_skip(session, filename=incoming.filename, source=incoming.source.value, reason="duplicate",
                    detail=f"An exact copy of “{document.filename}”, which the Hub already holds: {where}. "
                           "Nothing was added; if you meant a different document, send that one.",
                    document_id=document.id, external_ref=external_ref)
        return InboxReceipt(document=document, inbox_item=item_for(session, document.id), duplicate=True)

    suggestion, _ = await _suggest(session, document, incoming.filename, incoming.content, context_text, classify)
    item = InboxItem(
        document_id=document.id, context_text=context_text, external_ref=external_ref,
        suggested_category=suggestion.category.value if suggestion.category is not None else None,
        suggested_paper_kind=suggestion.paper_kind,
        confidence=suggestion.confidence, classifier_note=suggestion.note,
        guess_json=_guess_json(suggestion, document),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return InboxReceipt(document=document, inbox_item=item, duplicate=False)


async def _suggest(session: Session, document: Document, filename: str, content: bytes, context_text: Optional[str],
                   classify) -> tuple[CategorySuggestion, bool]:
    """What a paper is: rules first, then the AI inside a High run. The same path for a new arrival and for a paper
    guessed again (PLAN step 12b). The flag is False when the AI could not be asked (the reason is in the note)."""
    # 1. Known, recurring documents are recognised by rules, with no AI (D13).
    recognised = recognise(session, filename=filename, content=content, context_text=context_text)
    if recognised is not None:
        suggestion = CategorySuggestion(Category(recognised.category), recognised.paper_kind, 1.0,
                                      "Recognised from " + "; ".join(recognised.reasons))
    else:
        # 2. A paper no rule recognises is read ONCE, inside a High run, to decide what it is (decision D20):
        #    it may turn out to be a private Health paper, so it never goes to a lower level first.
        try:
            with ratchet_scope(Tier.HIGH):
                suggestion = await classify(document.file_path, context_text)
        except Exception as exc:
            # Broad by design: a classifier failure (route down, credits, bad response) must never lose the
            # document -- it arrives without a suggestion and a human chooses.
            problem = explain("Automatic sorting didn't work.", str(exc))
            note = problem.text + " Tell me what it is."
            failed = CategorySuggestion(None, None, 0.0, note)
            try:    # nobody is watching this step: keep it on the Problems page too (a down private route shows there)
                record_problem(session, area="Reading documents", ref=f"document:{document.id}",
                               problem=Problem(problem.what, problem.why, problem.next_step, "/inbox"),
                               technical=str(exc))
            except Exception:
                pass    # reporting a problem must never create another one
            return failed, False
    return suggestion, True


def _guess_json(suggestion: CategorySuggestion, document: Document) -> str:
    """The guess as kept: the filed name falls back to the day the paper ARRIVED (not the day it was guessed)."""
    import json
    from app.services.paper_names import name_for_guess
    guess = dict(getattr(suggestion, "guess", None) or {})
    if guess:
        if guess.get("new_kind"):
            kind_label = guess["new_kind"]
        elif suggestion.category is not None and is_implemented(suggestion.category):
            spec = get_spec(suggestion.category)
            kind_label = spec.kind_label(suggestion.paper_kind) if suggestion.paper_kind else spec.label
        else:
            kind_label = "Paper"
        guess["filed_name"] = name_for_guess(guess, kind_label, document.created_at)
    return json.dumps(guess, ensure_ascii=False)


class GuessFailed(Exception):
    """The AI could not be asked to guess again; the waiting paper keeps its old guess."""


async def guess_again(session: Session, document_id: int, classify=suggest_category_and_kind) -> InboxItem:
    """Guess a waiting paper again (PLAN step 12b: the September guesses predate step 8), through the same path."""
    from pathlib import Path
    document, item = _pending(session, document_id)
    content = Path(document.file_path).read_bytes()
    suggestion, asked = await _suggest(session, document, document.filename, content, item.context_text, classify)
    if not asked:
        raise GuessFailed(suggestion.note)            # the old guess stays; the reason is on the Problems page
    item.suggested_category = suggestion.category.value if suggestion.category is not None else None
    item.suggested_paper_kind = suggestion.paper_kind
    item.confidence, item.classifier_note = suggestion.confidence, suggestion.note
    item.guess_json = _guess_json(suggestion, document)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def suggested_category(item: InboxItem) -> Optional[Category]:
    """The suggestion as a registered Domain, or None if it no longer is one."""
    try:
        category = Category(item.suggested_category) if item.suggested_category else None
    except ValueError:
        return None
    return category if is_implemented(category) else None


def describe(category: Optional[Category], paper_kind: Optional[str]) -> Optional[str]:
    if not is_implemented(category):
        return None
    spec = get_spec(category)
    return f"{spec.label} › {spec.kind_label(paper_kind)}" if paper_kind else spec.label


def is_confident(item: InboxItem) -> bool:
    category = suggested_category(item)
    if category is None or (item.confidence or 0.0) < CONFIDENT_THRESHOLD:
        return False
    spec = get_spec(category)
    if item.suggested_paper_kind is None:
        return spec.reading_proposes_kind
    return item.suggested_paper_kind in {c.value for c in spec.kinds}


def guess_of(item: InboxItem) -> dict:
    import json
    try:
        guess = json.loads(item.guess_json or "{}")
    except ValueError:
        return {}
    return guess if isinstance(guess, dict) else {}


def suggestion_label(item: InboxItem) -> Optional[str]:
    """The card's "Best guess": a new area or kind the Hub proposes is named, marked new (PLAN step 12b)."""
    guess = guess_of(item)
    category = suggested_category(item)
    if category is None and guess.get("new_area") and guess.get("new_kind"):
        return f"{guess['new_area']} (new area) › {guess['new_kind']} (new)"
    if category is not None and item.suggested_paper_kind is None and guess.get("new_kind"):
        return f"{get_spec(category).label} › {guess['new_kind']} (new)"
    return describe(category, item.suggested_paper_kind)


def _entry(item: InboxItem, document: Document) -> InboxEntry:
    return InboxEntry(
        item=item, document=document, confident=is_confident(item),
        suggestion_label=suggestion_label(item),
    )


def entry_for(session: Session, document_id: int) -> Optional[InboxEntry]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    return _entry(item, document) if item is not None else None


def pending_entries(session: Session) -> list[InboxEntry]:
    rows = session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(Document.status == DocumentStatus.PENDING_REVIEW)
        .order_by(InboxItem.received_at, InboxItem.id)
    ).all()
    return [_entry(item, document) for item, document in rows]


def recently_reviewed(session: Session, limit: int = 20) -> list[tuple[InboxItem, Document]]:
    return list(session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(InboxItem.reviewed_at.is_not(None))
        .order_by(InboxItem.reviewed_at.desc())
        .limit(limit)
    ).all())


def _pending(session: Session, document_id: int) -> tuple[Document, InboxItem]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    if document is None or item is None:
        raise InboxError("That document isn't in the Inbox.")
    if document.status != DocumentStatus.PENDING_REVIEW:
        raise InboxError("That document has already been handled.")
    return document, item


async def approve(
    session: Session,
    document_id: int,
    *,
    category_value: str,
    paper_kind: Optional[str],
    fields: Mapping[str, str],
    reviewed_by: Optional[str],
    remember_sender: bool = False,
    remember_words: str = "",
    new_area: str = "",
    new_kind: str = "",
    guess_form=None,
) -> Document:
    """`guess_form`: the card's "What the Hub read" block as Pedro left it (PLAN step 12c); None keeps the guess."""
    document, item = _pending(session, document_id)
    made = _make_new_types(session, document, category_value, paper_kind, new_area, new_kind, reviewed_by)
    if made:
        category_value, paper_kind = made[0].area, made[-1].kind
    spec = _spec_or_none(category_value)
    guess = _guess_to_approve(session, item, guess_form, spec)
    if spec is not None and guess:                    # fields that repeat what the Hub read come from it (12d)
        from app.services.guess_card import field_values
        fields = dict(fields)
        for key, value in field_values(guess, spec.from_guess).items():
            if not str(fields.get(key) or "").strip():
                fields[key] = value
    try:
        filed = await _approve(session, document, item, category_value=category_value, paper_kind=paper_kind,
                               fields=fields, reviewed_by=reviewed_by, remember_sender=remember_sender,
                               remember_words=remember_words)
    except (InvalidClassification, TierHeld):
        if made:
            paper_types.take_back(session, made)      # a refused approval leaves no new type behind
        raise
    _keep_approved_guess(session, filed, item, guess, reviewed_by)
    return filed


def _spec_or_none(category_value: str):
    try:
        category = Category(category_value)
    except ValueError:
        return None
    return get_spec(category) if is_implemented(category) else None


def _guess_to_approve(session: Session, item: InboxItem, guess_form, spec) -> dict:
    """The guess as Pedro left it on the card (12c), checked like the AI's; items only where the area keeps them."""
    from app.services import guess_card
    guess = guess_of(item)
    keeps_items = bool(spec and spec.keeps_items)
    if guess_form is not None:
        guess = guess_card.from_form(session, guess_form, guess, keeps_items=keeps_items)
    if guess and not keeps_items:
        guess = {**guess, "items": []}                # rows of things belong to areas that keep them (House)
    return guess


def _keep_approved_guess(session: Session, document: Document, item: InboxItem, guess: dict,
                         by: Optional[str]) -> None:
    """The approved guess stays on the Inbox item, a new room or person is made, and the paper is filed under it:
    its new name, tags, who and items (PLAN steps 12c, 12d)."""
    import json
    from app.services import guess_card, paper_filing
    if not guess or document.category is None or not is_implemented(document.category):
        return
    spec = get_spec(document.category)
    guess_card.make_new(session, guess)
    kind_label = spec.kind_label(document.paper_kind) if document.paper_kind else spec.label
    final = guess_card.approved(guess, kind_label=kind_label, received=document.created_at, by=by)
    item.guess_json = json.dumps(final, ensure_ascii=False)
    session.add(item)
    paper_filing.file_under(session, document, final)
    session.commit()
    from app.services.paper_search import after_filing
    after_filing(session, document)                   # found by its new name, old name and tags (never raises)


def _make_new_types(session: Session, document: Document, category_value: str, paper_kind: Optional[str],
                    new_area: str, new_kind: str, by: Optional[str]) -> list:
    """The "+ New area" / "+ New kind of paper" choices (PLAN step 12a): create the type, then file under it."""
    from app.services.paper_types import NEW, AlreadyThere
    try:
        if category_value == NEW:
            if not " ".join(new_area.split()):
                raise InvalidClassification({"domain": "Not approved. The new area has no name yet. Type its name, "
                                                       "then approve again."})
            if not " ".join(new_kind.split()):
                raise InvalidClassification({"category": "Not approved. The new area needs its first kind of paper. "
                                                         "Type what kind of paper this is, then approve again."})
            area = paper_types.add_area(session, new_area, first_kind=new_kind, by=by, document_id=document.id)
            return [area, *[r for r in session.exec(select(PaperType).where(PaperType.area == area.area,
                                                                            PaperType.kind.is_not(None)))]]
        if paper_kind == NEW:
            if not " ".join(new_kind.split()):
                raise InvalidClassification({"category": "Not approved. The new kind of paper has no name yet. Type "
                                                         "its name, then approve again."})
            try:
                category = Category(category_value)
            except ValueError:
                raise InvalidClassification({"domain": "Choose a category"}) from None
            return [paper_types.add_kind(session, category, new_kind, by=by, document_id=document.id)]
    except AlreadyThere as exc:
        raise InvalidClassification({"category" if category_value != NEW else "domain":
                                     f"Not approved. {exc}. Choose it from the list instead, then approve again."}) from None
    return []


async def _approve(session: Session, document: Document, item: InboxItem, *, category_value: str,
                   paper_kind: Optional[str], fields: Mapping[str, str], reviewed_by: Optional[str],
                   remember_sender: bool, remember_words: str) -> Document:
    try:
        category = Category(category_value)
    except ValueError:
        raise InvalidClassification({"domain": "Choose a category"}) from None
    classification = Classification(category=category, paper_kind=paper_kind or None, fields=dict(fields))
    # Validate first so a rejected choice changes nothing (no review mark, no log).
    validated = validate_classification(session, classification, document.filename)
    if validated.paper_kind is None and not validated.spec.reading_proposes_kind:
        # The kind is decided once, here (PLAN step 2d, C3): no hidden second decision after approval.
        raise InvalidClassification({"category": "Choose what kind of paper it is"})
    require_ready(session, category, validated.paper_kind)    # raises TierHeld before anything is marked or read

    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Approved {document.filename} as {describe(category, classification.paper_kind)} "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'})",
        document_id=document.id, commit=False,
    )
    session.commit()

    filed = await finalize_document(session, document, classification)
    if remember_sender or remember_words.strip():
        remember(session, category=category.value, paper_kind=validated.paper_kind or "", by=reviewed_by,
                 sender=original_sender(item.context_text) if remember_sender else "",
                 words=remember_words, note=f"Remembered when {document.filename} was filed")
    return filed


def discard(session: Session, document_id: int, *, reviewed_by: Optional[str]) -> Document:
    """Raises PaperInUse when something already links to the waiting paper (work-list B7)."""
    document, item = _pending(session, document_id)
    refuse_if_used(session, document, what="Not discarded.")
    document.status = DocumentStatus.DISCARDED
    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(document)
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Discarded {document.filename} from the Inbox "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'}); file kept",
        document_id=document.id, commit=False,
    )
    session.commit()
    session.refresh(document)
    return document

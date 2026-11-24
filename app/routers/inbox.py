"""Routes for the shared, cross-domain Inbox (documents from email/Telegram
awaiting a human's first-time filing). All domain/category knowledge comes
from the registry; domain fields come from the generic fields layer -- no
domain is named here. DOCUMENT and RECORD categories are handled the same way
(finalize_document creates the Record). Re-filing after approval is Plan A's
/documents/{id}/edit and /records/{id}/edit."""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.categories.fields import InvalidClassification, build_form_fields, file_url, media_kind_for
from app.categories.entries import record_for_document
from app.categories.registry import document_url, get_spec, implemented_categories, is_implemented, record_url
from app.models.category import Category
from app.privacy.recognise import original_sender
from app.privacy.settings import TierHeld
from app.ui.same_person import section_html as same_person_section
from app.ui.to_check import section_html as to_check_section
from app.services import guess_card, inbox_service
from app.services.paper_types import NEW
from app.problems import Problem
from app.services.paper_uses import PaperInUse
from app.templating import templates

router = APIRouter(prefix="/inbox", tags=["inbox"])

_SELECTOR_KEYS = ("domain", "category", "remember", "remember_words", "new_area", "new_kind")


def _paper_fields(form: dict) -> dict:
    """The kind's own fields: neither the choosers nor the "What the Hub read" block (PLAN step 12c)."""
    return {k: v for k, v in form.items() if k not in _SELECTOR_KEYS and not k.startswith(guess_card.PREFIX)}


def _spec_for_value(category_value: Optional[str]):
    if category_value == NEW:
        return None
    try:
        category = Category(category_value) if category_value else None
    except ValueError:
        return None
    return get_spec(category) if is_implemented(category) else None


def _guess_view(session: Session, entry, spec, real_kind: Optional[str], new_kind_value: str, guess_source):
    """The "What the Hub read" block: the guess, or Pedro's edits so far when the form is re-drawn."""
    guess = inbox_service.guess_of(entry.item)
    if guess_source is not None:
        guess = guess_card.from_form(session, guess_source, guess, keeps_items=True)
    if not guess:
        return None, {}
    if new_kind_value:
        kind_label = new_kind_value
    elif spec is not None:
        kind_label = spec.kind_label(real_kind) if real_kind else spec.label
    else:
        kind_label = "Paper"
    return guess_card.view(session, guess, keeps_items=bool(spec and spec.keeps_items), kind_label=kind_label,
                           received=entry.document.created_at), guess


def _fields_context(session: Session, entry, category_value: Optional[str], paper_kind: Optional[str],
                    values: dict, errors: dict, guess_source=None) -> dict:
    spec = _spec_for_value(category_value)
    new_area = category_value == NEW
    new_kind = new_area or (spec is not None and paper_kind == NEW)
    if spec is None or paper_kind not in {c.value for c in spec.kinds}:
        paper_kind = NEW if new_kind else None   # changing the area resets the type
    real_kind = paper_kind if paper_kind != NEW else None
    form_fields = (
        build_form_fields(session, spec, real_kind, values, errors,
                          media_kind=media_kind_for(entry.document.filename))
        if spec is not None and (real_kind or spec.infers_kind) else []
    )
    guess_view, guess = _guess_view(session, entry, spec, real_kind, values.get("new_kind", "") if new_kind else "",
                                    guess_source)
    if spec is not None and guess_view is not None:   # what the Hub read is not asked again (PLAN step 12d)
        answered = guess_card.field_values(guess, spec.from_guess)
        form_fields = [f for f in form_fields if f.spec.key not in answered]
    return {
        "entry": entry,
        "domains": implemented_categories(),
        "selected_spec": spec,
        "selected_domain": NEW if new_area else (spec.category.value if spec else ""),
        "selected_category": paper_kind or "",
        "new_choice": NEW, "new_area": new_area, "new_kind": new_kind,
        "new_area_value": values.get("new_area", ""), "new_kind_value": values.get("new_kind", ""),
        "form_fields": form_fields,
        "errors": errors,
        "guess": guess_view,
    }


def _card_context(session: Session, entry, *, category_value=None, paper_kind=None,
                  values=None, errors=None, guess_source=None) -> dict:
    values = dict(values or {})
    if category_value is None:
        category_value, paper_kind, typed = _prefill(entry)
        values = {**typed, **values}
    return {
        "file_href": file_url(entry.document),
        "original_sender": original_sender(entry.item.context_text),
        **_fields_context(session, entry, category_value, paper_kind, values, errors or {}, guess_source),
    }


def _prefill(entry) -> tuple[Optional[str], Optional[str], dict]:
    """The guess pre-fills the card, a new area or kind typed in (PLAN step 12c, Claude 2026-11-24): nothing is
    filed until Pedro presses Approve, and the "not sure" line stays when the Hub is not sure."""
    item = entry.item
    guess = inbox_service.guess_of(item)
    known = inbox_service.suggested_category(item)
    if known is None and guess.get("new_area") and guess.get("new_kind"):
        return NEW, NEW, {"new_area": guess["new_area"], "new_kind": guess["new_kind"]}
    if known is not None and item.suggested_paper_kind is None and guess.get("new_kind"):
        return known.value, NEW, {"new_kind": guess["new_kind"]}
    if known is not None:
        return known.value, item.suggested_paper_kind, {}
    return None, None, {}


def _entry_or_404(session: Session, document_id: int):
    entry = inbox_service.entry_for(session, document_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not in the Inbox")
    return entry


# --- add a paper from the page, and the light approve card (PLAN step 10h) ----------------------------------------

MAX_UPLOAD_BYTES = inbox_service.MAX_PAGE_UPLOAD_BYTES
KEY_FIELDS = 3                      # the card shows "Filed as" and up to three of the kind's fields


@router.get("/add")
async def add_page(request: Request, problem: str = ""):
    return templates.TemplateResponse(request, "inbox/add.html",
                                      {"problem": inbox_service.PAGE_UPLOAD_PROBLEMS.get(problem)})


def get_inbox_classifier():
    """What reads a paper no rule recognises (inside a High run, decision D20). Tests override this dependency."""
    return inbox_service.suggest_category_and_kind


@router.post("/add")
async def add_paper(request: Request, file: UploadFile = File(None), note: str = Form(""),
                    session: Session = Depends(get_session), classify=Depends(get_inbox_classifier)):
    """A paper added from the page goes to the Inbox exactly as one sent by email or Telegram (same recognition,
    same reading, same approval), then opens its approve card."""
    content = await file.read(MAX_UPLOAD_BYTES + 1) if file is not None and file.filename else b""
    try:
        receipt = await inbox_service.add_from_page(
            session, file.filename if file is not None else None, content,
            uploaded_by=getattr(request.state, "user_email", None), note=note, classify=classify)
    except inbox_service.UploadRefused as exc:
        return RedirectResponse(f"/inbox/add?problem={exc.key}", status_code=303)
    return RedirectResponse(f"/inbox/{receipt.document.id}/card" + ("?duplicate=1" if receipt.duplicate else ""),
                            status_code=303)


def _light_card(session: Session, entry, values: Optional[dict] = None, errors: Optional[dict] = None,
                guess_source=None) -> dict:
    ctx = _card_context(session, entry, values=values, errors=errors, guess_source=guess_source)
    fields = ctx["form_fields"]
    required = [f for f in fields if f.spec.required]
    shown = (required or fields)[:KEY_FIELDS]
    return {**ctx, "key_fields": shown, "too_many": len(required) > KEY_FIELDS,
            "media": media_kind_for(entry.document.filename).value}


@router.get("/{document_id}/card")
async def approve_card(request: Request, document_id: int, duplicate: int = 0,
                       session: Session = Depends(get_session)):
    entry = inbox_service.entry_for(session, document_id)
    if entry is None or entry.document.status.value != "pending_review":
        from app.models.document import Document
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail={
                "what": "Nothing to approve.", "why": "That paper is not in the Hub.",
                "next_step": "Add it again from the + button."})
        return templates.TemplateResponse(request, "inbox/card.html", {
            "filed": document, "in_place": document.category is not None, "duplicate": bool(duplicate),
            "label": inbox_service.describe(document.category, document.paper_kind),
            "document_href": document_url(document) if document.category else "/inbox"})
    return templates.TemplateResponse(request, "inbox/card.html", {**_light_card(session, entry),
                                                                   "duplicate": bool(duplicate)})


@router.post("/{document_id}/card")
async def approve_from_card(request: Request, document_id: int, session: Session = Depends(get_session)):
    """Approve from the light card: the Inbox's own approval, unchanged; a refusal comes back on the card."""
    entry = _entry_or_404(session, document_id)
    raw = await request.form()
    form = {k: v for k, v in raw.items() if isinstance(v, str)}
    category_value, category = form.get("domain", ""), form.get("category", "") or None
    fields = _paper_fields(form)
    try:
        document = await inbox_service.approve(
            session, document_id, category_value=category_value, paper_kind=category, fields=fields,
            reviewed_by=getattr(request.state, "user_email", None),
            new_area=form.get("new_area", ""), new_kind=form.get("new_kind", ""), guess_form=raw)
    except TierHeld:
        return templates.TemplateResponse(request, "inbox/card.html",
                                          {**_light_card(session, entry, fields, guess_source=raw), "held": True},
                                          status_code=200)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "inbox/card.html",
                                          {**_light_card(session, entry, fields, exc.errors, guess_source=raw)})
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail={
            "what": "Not approved.", "why": str(exc), "next_step": "Open the Inbox to see where the paper is now."})
    record = record_for_document(session, document)
    return templates.TemplateResponse(request, "inbox/card.html", {
        "filed": document, "approved": True, "label": inbox_service.describe(document.category, document.paper_kind),
        "document_href": record_url(record) if record is not None else document_url(document)})


@router.get("")
async def inbox_page(request: Request, session: Session = Depends(get_session)):
    card = templates.get_template("inbox/_entry.html")
    cards = [card.render(**_card_context(session, e)) for e in inbox_service.pending_entries(session)]
    recent = [
        {"item": item, "document": document,
         "label": inbox_service.describe(document.category, document.paper_kind)}
        for item, document in inbox_service.recently_reviewed(session)
    ]
    skipped = [{"skip": s, "document": d, "href": (document_url(d) if d is not None and d.category is not None else "/inbox")}
               for s, d in inbox_service.recent_skips(session)]
    return templates.TemplateResponse(request, "inbox/list.html",
                                      {"cards": cards, "recent": recent, "skipped": skipped,
                                       "to_check_section": to_check_section(session),
                                       "same_person_section": same_person_section(session)})


@router.get("/{document_id}/fields")
async def fields_partial(request: Request, document_id: int, domain: Optional[str] = None,
                         category: Optional[str] = None, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    query = request.query_params
    typed = {k: query.get(k, "") for k in ("new_area", "new_kind")}
    return templates.TemplateResponse(
        request, "inbox/_fields.html", _fields_context(session, entry, domain, category, typed, {}, query)
    )


@router.get("/{document_id}/item-row")
async def item_row(request: Request, document_id: int, session: Session = Depends(get_session)):
    """One empty row for "+ Add an item" (PLAN step 12c)."""
    entry = _entry_or_404(session, document_id)
    return templates.TemplateResponse(request, "inbox/_item_row.html", {"entry": entry, "row": guess_card.blank_row()})


@router.post("/{document_id}/approve")
async def approve(request: Request, document_id: int, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    raw = await request.form()
    form = {k: v for k, v in raw.items() if isinstance(v, str)}
    category_value, category = form.get("domain", ""), form.get("category", "") or None
    fields = _paper_fields(form)
    try:
        document = await inbox_service.approve(
            session, document_id, category_value=category_value, paper_kind=category, fields=fields,
            reviewed_by=getattr(request.state, "user_email", None),
            new_area=form.get("new_area", ""), new_kind=form.get("new_kind", ""), guess_form=raw,
            remember_sender=form.get("remember") == "1" and bool(original_sender(entry.item.context_text)),
            remember_words=form.get("remember_words", "") if form.get("remember") == "1" else "",
        )
    except TierHeld:
        ctx = _card_context(session, entry, category_value=category_value, paper_kind=category, values=fields, errors={},
                            guess_source=raw)
        return templates.TemplateResponse(request, "inbox/_entry.html", {**ctx, "held": True}, status_code=409)
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidClassification as exc:
        typed = {**fields, "new_area": form.get("new_area", ""), "new_kind": form.get("new_kind", "")}
        ctx = _card_context(session, entry, category_value=category_value, paper_kind=category,
                            values=typed, errors=exc.errors, guess_source=raw)
        return templates.TemplateResponse(request, "inbox/_entry.html", ctx, status_code=422)

    record = record_for_document(session, document)  # RECORD category => link the record, not the attachment
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": False,
        "label": inbox_service.describe(document.category, document.paper_kind),
        "document_href": record_url(record) if record is not None else document_url(document),
    })


@router.post("/{document_id}/discard")
async def discard(request: Request, document_id: int, session: Session = Depends(get_session)):
    _entry_or_404(session, document_id)
    try:
        document = inbox_service.discard(session, document_id,
                                         reviewed_by=getattr(request.state, "user_email", None))
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except PaperInUse as refused:
        raise HTTPException(status_code=409, detail=refused.problem.as_detail()) from None
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": True, "label": None, "document_href": None,
    })

"""Routes for the shared, cross-domain Inbox (documents from email/Telegram
awaiting a human's first-time filing). All domain/category knowledge comes
from the registry; domain fields come from the generic fields layer -- no
domain is named here. DOCUMENT and RECORD categories are handled the same way
(finalize_document creates the Record). Re-filing after approval is Plan A's
/documents/{id}/edit and /records/{id}/edit."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.db import get_session
from app.domains.fields import InvalidClassification, build_form_fields, file_url, media_kind_for
from app.domains.entries import record_for_document
from app.domains.registry import document_url, get_spec, implemented_domains, is_implemented, record_url
from app.models.domain import Domain
from app.services import inbox_service
from app.templating import templates

router = APIRouter(prefix="/inbox", tags=["inbox"])

_SELECTOR_KEYS = ("domain", "category")


def _spec_for_value(domain_value: Optional[str]):
    try:
        domain = Domain(domain_value) if domain_value else None
    except ValueError:
        return None
    return get_spec(domain) if is_implemented(domain) else None


def _fields_context(session: Session, entry, domain_value: Optional[str], category: Optional[str],
                    values: dict, errors: dict) -> dict:
    spec = _spec_for_value(domain_value)
    if spec is None or category not in {c.value for c in spec.categories}:
        category = None   # changing the area resets the type
    form_fields = (
        build_form_fields(session, spec, category, values, errors,
                          media_kind=media_kind_for(entry.document.filename))
        if spec is not None and (category or spec.infers_category) else []
    )
    return {
        "entry": entry,
        "domains": implemented_domains(),
        "selected_spec": spec,
        "selected_domain": spec.domain.value if spec else "",
        "selected_category": category or "",
        "form_fields": form_fields,
        "errors": errors,
    }


def _card_context(session: Session, entry, *, domain_value=None, category=None,
                  values=None, errors=None) -> dict:
    if domain_value is None and entry.confident:  # confident => pre-filled
        domain_value = entry.item.suggested_domain
        category = entry.item.suggested_category
    return {
        "file_href": file_url(entry.document),
        **_fields_context(session, entry, domain_value, category, values or {}, errors or {}),
    }


def _entry_or_404(session: Session, document_id: int):
    entry = inbox_service.entry_for(session, document_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Not in the Inbox")
    return entry


@router.get("")
async def inbox_page(request: Request, session: Session = Depends(get_session)):
    card = templates.get_template("inbox/_entry.html")
    cards = [card.render(**_card_context(session, e)) for e in inbox_service.pending_entries(session)]
    recent = [
        {"item": item, "document": document,
         "label": inbox_service.describe(document.domain, document.category)}
        for item, document in inbox_service.recently_reviewed(session)
    ]
    return templates.TemplateResponse(request, "inbox/list.html", {"cards": cards, "recent": recent})


@router.get("/{document_id}/fields")
async def fields_partial(request: Request, document_id: int, domain: Optional[str] = None,
                         category: Optional[str] = None, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    return templates.TemplateResponse(
        request, "inbox/_fields.html", _fields_context(session, entry, domain, category, {}, {})
    )


@router.post("/{document_id}/approve")
async def approve(request: Request, document_id: int, session: Session = Depends(get_session)):
    entry = _entry_or_404(session, document_id)
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    domain_value, category = form.get("domain", ""), form.get("category", "") or None
    fields = {k: v for k, v in form.items() if k not in _SELECTOR_KEYS}
    try:
        document = await inbox_service.approve(
            session, document_id, domain_value=domain_value, category=category, fields=fields,
            reviewed_by=getattr(request.state, "user_email", None),
        )
    except inbox_service.InboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidClassification as exc:
        ctx = _card_context(session, entry, domain_value=domain_value, category=category,
                            values=fields, errors=exc.errors)
        return templates.TemplateResponse(request, "inbox/_entry.html", ctx, status_code=422)

    record = record_for_document(session, document)  # RECORD category => link the record, not the attachment
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": False,
        "label": inbox_service.describe(document.domain, document.category),
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
    return templates.TemplateResponse(request, "inbox/_result.html", {
        "document": document, "discarded": True, "label": None, "document_href": None,
    })

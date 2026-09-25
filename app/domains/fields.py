"""Per-document domain metadata ("fields").

Values are validated against the owning domain's FieldSpecs and stored as a
JSON object of strings in Document.fields_json (dates as ISO YYYY-MM-DD).
The read helpers here are domain-agnostic on purpose: the House tab, Plan
B's Inbox edit form and Plan C's Ask all render/describe any domain's
documents through them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from sqlalchemy import func, select as sa_select
from sqlmodel import Session

from app.domains.base import DomainSpec, FieldKind, FieldSpec, MediaKind
from app.domains.registry import document_url, get_spec, is_implemented
from app.models.document import Document
from app.models.domain import Domain
from app.models.record import Record

_EXTENSION_MEDIA = {
    ".pdf": MediaKind.PDF,
    ".png": MediaKind.IMAGE, ".jpg": MediaKind.IMAGE, ".jpeg": MediaKind.IMAGE,
    ".heic": MediaKind.IMAGE, ".webp": MediaKind.IMAGE,
    ".mp4": MediaKind.VIDEO, ".mov": MediaKind.VIDEO, ".m4v": MediaKind.VIDEO,
    ".webm": MediaKind.VIDEO, ".3gp": MediaKind.VIDEO,
}


class InvalidClassification(ValueError):
    """A classification (domain / category / file type / fields) that the
    registry rejects. `errors` maps "domain", "category", "file" or a field
    key to a human-readable message, ready to show next to a form input."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def media_kind_for(filename: str) -> MediaKind:
    return _EXTENSION_MEDIA.get(Path(filename).suffix.lower(), MediaKind.OTHER)


def load_fields(document: Document | Record) -> dict[str, str]:
    return json.loads(document.fields_json or "{}")


def dump_fields(fields: Mapping[str, str]) -> str:
    return json.dumps(dict(fields), sort_keys=True, ensure_ascii=False)


def field_date(fields: Mapping[str, str], key: str) -> Optional[date]:
    value = fields.get(key)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def distinct_field_values(session: Session, domain: Domain, key: str) -> list[str]:
    """Previously used values of one field within a domain (case-insensitively
    de-duplicated, first spelling wins) -- the options behind SUGGEST fields."""
    if not key.isidentifier():
        raise ValueError(f"Invalid field key: {key!r}")
    value = func.json_extract(Document.fields_json, f"$.{key}")
    rows = session.execute(
        sa_select(value).where(Document.domain == domain, value.is_not(None)).order_by(Document.id)
    ).scalars().all()
    record_value = func.json_extract(Record.fields_json, f"$.{key}")
    record_rows = session.execute(
        sa_select(record_value).where(Record.domain == domain, Record.retired_at.is_(None), record_value.is_not(None))
        .order_by(Record.id)
    ).scalars().all()
    seen: dict[str, str] = {}
    for raw in [*rows, *record_rows]:
        if isinstance(raw, str) and raw.strip():
            seen.setdefault(raw.strip().lower(), raw.strip())
    return sorted(seen.values(), key=str.lower)


def validate_fields(
    session: Session,
    spec: DomainSpec,
    category: Optional[str],
    media_kind: Optional[MediaKind],
    raw: Mapping[str, Any],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    clean: dict[str, str] = {}
    for field_spec in spec.fields_for(category, media_kind):
        value = raw.get(field_spec.key)
        text = "" if value is None else str(value).strip()
        if not text:
            if field_spec.required:
                errors[field_spec.key] = f"{field_spec.label} is required"
            continue
        if field_spec.kind == FieldKind.DATE:
            try:
                clean[field_spec.key] = date.fromisoformat(text).isoformat()
            except ValueError:
                errors[field_spec.key] = f"{field_spec.label} must be a date (YYYY-MM-DD)"
        elif field_spec.kind == FieldKind.CHOICE:
            allowed = {option for option, _ in field_spec.options(session)}
            if text in allowed:
                clean[field_spec.key] = text
            else:
                errors[field_spec.key] = f"Choose a valid {field_spec.label.lower()}"
        elif field_spec.kind == FieldKind.SUGGEST:
            collapsed = " ".join(text.split())
            existing = {v.lower(): v for v in distinct_field_values(session, spec.domain, field_spec.key)}
            clean[field_spec.key] = existing.get(collapsed.lower(), collapsed)
        else:
            clean[field_spec.key] = text
    if errors:
        raise InvalidClassification(errors)
    return clean


def effective_fields(spec: DomainSpec, category: Optional[str], fields: Mapping[str, str]) -> dict[str, str]:
    """Stored fields plus the domain's derived fields (never stored)."""
    return {**fields, **spec.derive_fields(category, dict(fields))}


def file_url(document: Document) -> str:
    return f"/static/documents/{Path(document.file_path).name}"


@dataclass
class DocumentDescription:
    document_id: int
    filename: str
    file_url: str
    url: Optional[str]
    domain_label: Optional[str]
    category_label: Optional[str]
    status: str
    created_at: datetime
    fields: list[tuple[str, str]]  # (label, value), in the domain's declared field order


def describe_document(document: Document) -> DocumentDescription:
    fields = load_fields(document)
    if is_implemented(document.domain):
        spec = get_spec(document.domain)
        labelled = [(f.label, fields[f.key]) for f in spec.fields if f.key in fields]
        domain_label = spec.label
        category_label = spec.category_label(document.category) or None
    else:
        labelled = sorted(fields.items())
        domain_label = None
        category_label = document.category
    return DocumentDescription(
        document_id=document.id,
        filename=document.filename,
        file_url=file_url(document),
        url=document_url(document),
        domain_label=domain_label,
        category_label=category_label,
        status=document.status.value,
        created_at=document.created_at,
        fields=labelled,
    )


@dataclass
class FormField:
    spec: FieldSpec
    value: str
    error: Optional[str]
    options: list[tuple[str, str]]  # CHOICE options, or SUGGEST suggestions


def build_form_fields(
    session: Session,
    spec: DomainSpec,
    category: Optional[str],
    values: Mapping[str, Any],
    errors: Mapping[str, str],
    media_kind: Optional[MediaKind] = None,
) -> list[FormField]:
    form_fields = []
    for field_spec in spec.fields_for(category, media_kind):
        if field_spec.kind == FieldKind.CHOICE:
            options = field_spec.options(session)
        elif field_spec.kind == FieldKind.SUGGEST:
            options = [(v, v) for v in distinct_field_values(session, spec.domain, field_spec.key)]
        else:
            options = []
        form_fields.append(FormField(
            spec=field_spec,
            value=str(values.get(field_spec.key) or ""),
            error=errors.get(field_spec.key),
            options=options,
        ))
    return form_fields
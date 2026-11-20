"""Routes for browsing and filtering transactions."""

from typing import Optional
from urllib.parse import urlencode

import json
from markupsafe import escape
from fastapi.responses import HTMLResponse
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from app.baseline import baseline_note
from app.db import get_session
from app.models.account import Account
from app.models.category_node import CategoryNode
from app.models.commitment import Cadence
from app.models.debt import Debt, DebtDirection
from app.models.person import Person
from app.services import debt_ledger as ledger
from app.models.merchant import Merchant
from app.models.transaction import Nature, Transaction, TransactionType
from app.problems import Problem, notice, refusal_response
from app.services.classification_engine import get_needs_review_page, REVIEW_SECTIONS
from app.services.merchant_assign import assign_category, list_merchants
from app.services.taxonomy import (
    node_options as tree_options,
    get_node,
)
from app.services import transaction_list
from app.services.transaction_list import (
    PAGE_SIZE, UnknownCategory, commitment_from_merchant, attached_documents, count_filtered_transactions, filtered_transactions, lookup_dicts_for, node_context,
)
from app.templating import templates

router = APIRouter(prefix="/financials/transactions", tags=["transactions"])

def _int(value, default=None):
    """A blank or malformed query number (a form sends `account_id=`) means 'not set', not a 422."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@router.get("")
async def list_transactions(
    request: Request,
    nature: Optional[str] = None,
    account_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[str] = None,
    debt_id: Optional[str] = None,
    transaction_type: Optional[str] = None,
    page: Optional[str] = "1",
    transaction_id: Optional[str] = None,
    merchant_id: Optional[str] = None,
    category_node: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    session: Session = Depends(get_session),
):
    account_id, commitment_id, debt_id = _int(account_id), _int(commitment_id), _int(debt_id)
    transaction_id, merchant_id, page = _int(transaction_id), _int(merchant_id), max(1, _int(page, 1))
    nature, date_from, date_to = (nature or None), (date_from or None), (date_to or None)
    transaction_type, category_node, tag, q = (transaction_type or None), (category_node or None), (tag or None), ((q or "").strip() or None)
    transactions = filtered_transactions(
        session, nature, account_id, date_from, date_to,
        commitment_id=commitment_id, debt_id=debt_id, transaction_type=transaction_type,
        page=page, transaction_id=transaction_id, merchant_id=merchant_id,
        category_node=category_node, tag=tag, q=q,
    )
    total_count = count_filtered_transactions(
        session, nature, account_id, date_from, date_to,
        commitment_id=commitment_id, debt_id=debt_id, transaction_type=transaction_type,
        transaction_id=transaction_id, merchant_id=merchant_id,
        category_node=category_node, tag=tag, q=q,
    )
    total_pages = max(1, -(-total_count // PAGE_SIZE))
    accounts = session.exec(select(Account)).all()
    merchant_names, account_names, linked_transactions = lookup_dicts_for(session, transactions)
    node_labels, row_flow = node_context(session, transactions)
    merchant_filter_name = merchant_names.get(merchant_id) if merchant_id else None
    if merchant_id and merchant_filter_name is None:
        merchant = session.get(Merchant, merchant_id)
        merchant_filter_name = merchant.canonical_name if merchant else None

    # Get all tags for the filter dropdown
    from app.services.tag_service import all_tags
    tags = all_tags(session)

    return templates.TemplateResponse(
        request,
        "transactions/list.html",
        {
            "baseline_note": baseline_note(session),
            "transactions": transactions,
            "accounts": accounts,
            "natures": list(Nature),
            "filters": {
                "nature": nature, "account_id": account_id,
                "date_from": date_from, "date_to": date_to,
                "commitment_id": commitment_id, "debt_id": debt_id,
                "transaction_type": transaction_type, "merchant_id": merchant_id,
                "category_node": category_node, "tag": tag, "q": q,
            },
            "filters_qs": urlencode({k: v for k, v in {
                "category_node": category_node, "tag": tag, "nature": nature,
                "account_id": account_id, "date_from": date_from, "date_to": date_to, "commitment_id": commitment_id,
                "debt_id": debt_id, "transaction_type": transaction_type, "merchant_id": merchant_id, "q": q,
            }.items() if v not in (None, "")}),
            "merchant_filter_name": merchant_filter_name,
            "page": page,
            "total_pages": total_pages,
            "merchant_names": merchant_names,
            "account_names": account_names,
            "linked_transactions": linked_transactions,
            "node_options": tree_options(session),
            "node_labels": node_labels, "attached": attached_documents(session, transactions),
            "row_flow": row_flow,
            "tags": tags,
        },
    )

@router.post("/bulk-edit")
async def bulk_edit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    transaction_ids = [int(v) for v in form.getlist("transaction_ids")]
    new_category_node = form.get("new_category_node") or None
    new_nature = form.get("new_nature") or None
    new_account_id = form.get("new_account_id") or None

    filter_category_node = form.get("category_node") or None
    filter_tag = form.get("tag") or None
    filter_q = form.get("q") or None
    filter_nature = form.get("nature") or None
    filter_account_id = form.get("account_id") or None
    filter_date_from = form.get("date_from") or None
    filter_date_to = form.get("date_to") or None
    filter_page = form.get("page") or None

    if transaction_ids:
        try:
            transaction_list.bulk_edit(session, transaction_ids, new_category_node, new_nature, new_account_id)
        except UnknownCategory:
            raise HTTPException(status_code=400, detail="Unknown category")

    transactions = filtered_transactions(
        session,
        category_node=filter_category_node,
        tag=filter_tag,
        q=filter_q,
        nature=filter_nature,
        account_id=int(filter_account_id) if filter_account_id else None,
        date_from=filter_date_from,
        date_to=filter_date_to,
        page=int(filter_page) if filter_page else 1,
    )
    merchant_names, account_names, linked_transactions = lookup_dicts_for(session, transactions)
    node_labels, row_flow = node_context(session, transactions)
    return templates.TemplateResponse(
        request,
        "transactions/_rows.html",
        {
            "transactions": transactions, "merchant_names": merchant_names, "account_names": account_names,
            "linked_transactions": linked_transactions, "node_labels": node_labels, "row_flow": row_flow,
            "attached": attached_documents(session, transactions),
        },
    )

def _debt_candidate_options(session: Session, candidates: list) -> dict:
    """Per debt candidate: the informal debts it can be added to (person, direction,
    balance and the role it would get) and the direction a new debt would default to."""
    people = {p.id: p.name for p in session.exec(select(Person)).all()}
    debts = session.exec(
        select(Debt).where(Debt.external_number.is_(None), Debt.status == "active").order_by(Debt.id)
    ).all()
    out = {}
    for t in candidates:
        direction = ledger.derived_direction(t.transaction_type)
        out[t.id] = {
            "direction": direction.value if direction else None,
            "debts": [
                {"id": d.id, "person": people.get(d.person_id) or "Unnamed",
                 "lent": d.direction == DebtDirection.OWED_TO_US, "balance": d.current_balance,
                 "role": ledger.default_kind(d.direction, t.transaction_type),
                 "suggested": ledger.transfer_default_kind(d.direction)}
                for d in debts
            ],
        }
    return out


def _review_offsets(source) -> dict[str, int]:
    """The page the user is on in each Needs Review list (m_off, r_off, ...), taken
    from the query string or the form so an action re-renders the same page."""
    out = {}
    for key in REVIEW_SECTIONS:
        raw = str(source.get(f"{key}_off") or "0")
        out[key] = int(raw) if raw.isdigit() else 0
    return out


def _needs_review_context(session: Session, offsets: Optional[dict[str, int]] = None) -> dict:
    page = get_needs_review_page(session, offsets)
    queue = page.queue
    nodes = tree_options(session)
    # Rows carry only the suggested node; the full list loads on first focus.
    return {
        "queue": queue,
        "sections": page.sections,
        "offsets_json": json.dumps({f"{k}_off": v.offset for k, v in page.sections.items()}),
        "debt_options": _debt_candidate_options(session, queue.debt_candidates),
        "node_by_id": {o["id"]: o for o in nodes},
        "natures": list(Nature),
    }


def _review_rows(request: Request, session: Session, source=None):
    offsets = _review_offsets(source) if source is not None else None
    return templates.TemplateResponse(
        request, "transactions/_needs_review_rows.html", _needs_review_context(session, offsets)
    )


@router.get("/needs-review")
async def needs_review(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "transactions/needs_review.html",
        {**_needs_review_context(session, _review_offsets(request.query_params)),
         "baseline_note": baseline_note(session)},
    )


@router.get("/needs-review/rows")
async def needs_review_rows(request: Request, session: Session = Depends(get_session)):
    return _review_rows(request, session, request.query_params)


@router.get("/node-options")
async def node_options(session: Session = Depends(get_session)):
    """<option> elements for every level-3 node, grouped per level-1 group, loaded
    by a Needs Review select the first time it is focused."""
    groups: dict[str, list[str]] = {}
    for o in tree_options(session):
        if o["level"] != 3 or o["slug"].startswith("unsorted"):
            continue
        group = o["label"].split(" \u203a ")[0]
        groups.setdefault(group, []).append(
            f'<option value="{escape(o["slug"])}">{escape(o["label"])}</option>'
        )
    body = [f'<option value="">(choose)</option>']
    for group, rows in groups.items():
        body.append(f'<optgroup label="{escape(group)}">{"".join(rows)}</optgroup>')
    return HTMLResponse("".join(body), headers={"Cache-Control": "private, max-age=600"})

@router.post("/merchants/{merchant_id}/confirm")
async def confirm_merchant(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")

    form = await request.form()
    node_slug = form.get("category_node") or None
    new_nature = form.get("nature") or None
    if node_slug:
        try:
            node = get_node(session, node_slug)
        except NoResultFound:
            raise HTTPException(status_code=400, detail="Unknown category")
        # One click fixes the merchant's history: entries not yet filed (or in Unsorted) are
        # filed under the node; with "also move earlier entries" the ones filed elsewhere move
        # too. Loan-linked entries, transfers and credits that are no refund are never moved.
        assign_category(session, [merchant.id], node,
                        refile_existing=form.get("refile_existing") in ("1", "on", "true"), confirm=False)
        from app.services import rules     # one memory of past answers (PLAN step 2): the person's answer is kept
        rules.remember_merchant(session, merchant, node, by=getattr(request.state, "user_email", None))
    if new_nature:
        merchant.default_nature = Nature(new_nature)

    # No category chosen and none on record: the merchant still needs one, so
    # it must not drop out of the review queue.
    if node_slug or merchant.default_category_id is not None:
        merchant.confirmed = True
    session.add(merchant)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/merchants/{merchant_id}/dismiss-recurring")
async def dismiss_recurring(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")
    merchant.recurring_reviewed = True
    session.add(merchant)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/{transaction_id}/dismiss-debt-candidate")
async def dismiss_debt_candidate(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    transaction.debt_candidate_reviewed = True
    session.add(transaction)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/merchants/{merchant_id}/create-commitment")
async def create_commitment_from_merchant(
    request: Request, merchant_id: int, session: Session = Depends(get_session)
):
    form = await request.form()
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")

    commitment_from_merchant(session, merchant, Cadence(form["cadence"]), float(form["planned_amount"]),
                             int(form["year"]) if form.get("year") else None)

    return _review_rows(request, session, form)

def _plain(request: Request, status: int, message: str):
    """A refusal: the page's banner for a button press, the plain text otherwise (app/problems.py)."""
    return refusal_response(request, status, message)


@router.post("/{transaction_id}/link-debt")
async def link_debt(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        return _plain(request, 404, notice("Transaction not found", "Not linked."))
    if transaction.review_status in ("needs_check", "rejected"):
        return _plain(request, 409, Problem(
            "Not linked.", "This line is waiting for a check (or was marked as not a real line), so it counts nowhere yet "
            "and a debt balance built on it would be wrong.",
            "Decide it first in the To check list (Looks right or Fix the value), then link it.").text)
    role = (form.get("role") or "").strip() or None
    if role is not None and role not in ledger.ROLES:
        return _plain(request, 400, notice("The role must be advance or repayment", "Not linked."))
    if transaction.debt_id is not None:
        return _plain(request, 400, notice("This transaction is already linked to a debt", "Not linked."))

    existing_debt_id = (form.get("existing_debt_id") or "").strip()
    if existing_debt_id:
        debt = session.get(Debt, int(existing_debt_id)) if existing_debt_id.isdigit() else None
        if debt is None:
            return _plain(request, 404, notice("Debt not found", "Not linked."))
        if debt.external_number is not None:
            # A statement loan: a plain link, no ledger and no counterparty rule.
            transaction.debt_id = debt.id
            transaction.debt_candidate_reviewed = True
            session.add(transaction)
            session.commit()
            return _review_rows(request, session, form)
        entry_kind = role
    else:
        direction = form.get("direction") or None
        try:
            direction = DebtDirection(direction) if direction else ledger.derived_direction(transaction.transaction_type)
        except ValueError:
            return _plain(request, 400, notice("Unknown direction", "Not linked."))
        if direction is None:
            return _plain(request, 400, notice("Choose the direction: a transfer does not say whether you lent or borrowed", "Not linked."))
        person_name = (form.get("person_name") or "").strip()
        if len(person_name) > 80:
            return _plain(request, 400, notice("The name is at most 80 characters", "Not linked."))
        entry_kind = role or "advance"  # the creating transaction is the loan's first advance
        if transaction.transaction_type != TransactionType.TRANSFER \
                and ledger.default_kind(direction, transaction.transaction_type) != entry_kind:
            return _plain(request, 400, notice("The role does not fit this transaction and direction: money going out "
                                      "starts a loan we gave, money coming in starts a loan we received", "Not linked."))
        try:
            debt = ledger.create_informal_debt(session, person_name, direction)
        except ValueError as exc:
            session.rollback()
            return _plain(request, 400, notice(str(exc), "Not linked."))

    try:
        ledger.link_transaction_as_entry(session, debt, transaction, entry_kind)
    except ValueError as exc:
        session.rollback()
        return _plain(request, 400, notice(str(exc), "Not linked."))

    # Informal debts learn the counterparty, so later transactions from it
    # link automatically; statement loans never get rules.
    ledger.learn_counterparty(session, debt, transaction)
    session.commit()

    return _review_rows(request, session, form)


# ---- Bulk classification: pick many merchants (or provider texts), give them one category ----

def _leaf_groups(session: Session) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for o in tree_options(session):
        if o["level"] == 3 and not o["slug"].startswith("unsorted"):
            groups.setdefault(o["label"].split(" \u203a ")[0], []).append(o)
    return list(groups.items())


def _date(value):
    from datetime import date as _d
    try:
        return _d.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bulk_view(params) -> dict:
    """The page's filters, read leniently from a query string or a posted form (blank = not set)."""
    get = lambda k, d="": (params.get(k) or d)  # noqa: E731
    scope = get("scope", "undecided")
    return {
        "by": "provider" if get("by") == "provider" else "merchant",
        "q": get("q").strip(), "scope": scope if scope in ("all", "undecided") else "undecided",
        "cat": get("cat"), "min": max(1, _int(get("min"), 1)),
        "date_from": get("date_from"), "date_to": get("date_to"),
        "sort": get("sort", "entries"), "page": max(1, _int(get("page"), 1)),
    }


def _view_qs(view: dict, **extra) -> str:
    return urlencode({k: v for k, v in {**view, **extra}.items() if v not in ("", None)})


@router.get("/bulk")
async def bulk_page(request: Request, session: Session = Depends(get_session)):
    from app.services.provider_rules import list_provider_groups
    params = request.query_params
    view = _bulk_view(params)
    common = dict(q=view["q"], sort=view["sort"], page=view["page"], node_slug=view["cat"],
                  date_from=_date(view["date_from"]), date_to=_date(view["date_to"]), min_entries=view["min"])
    if view["by"] == "provider":
        result = list_provider_groups(session, **common)
    else:
        result = list_merchants(session, scope=view["scope"], **common)
    view["page"] = result.page
    applied = _int(params.get("applied"))
    return templates.TemplateResponse(request, "transactions/bulk.html", {
        "baseline_note": baseline_note(session),
        "result": result, "view": view, "view_qs": _view_qs({k: v for k, v in view.items() if k != "page"}),
        "qs_nb": _view_qs({k: v for k, v in view.items() if k not in ("page", "by")}),
        "leaf_groups": _leaf_groups(session), "node_options": tree_options(session),
        "banner": None if applied is None else {"n": applied, "moved": _int(params.get("moved"), 0),
                                                "filed": _int(params.get("filed"), 0), "rules": _int(params.get("rules"), 0),
                                                "by": view["by"]},
    })


@router.post("/bulk")
async def bulk_apply(request: Request, session: Session = Depends(get_session)):
    from fastapi.responses import RedirectResponse
    from app.services.provider_rules import apply_to_providers
    form = await request.form()
    view = _bulk_view(form)
    slug = form.get("category_node") or ""
    refile = form.get("refile_existing") in ("1", "on", "true")
    apply_now = form.get("step") == "apply"
    try:
        node = get_node(session, slug)
    except NoResultFound:
        raise HTTPException(status_code=400, detail="Pick a sub-category to apply")
    try:
        if view["by"] == "provider":
            keys = sorted({v for v in form.getlist("group_keys") if v})
            if not keys:
                raise HTTPException(status_code=400, detail="Tick at least one description")
            report = apply_to_providers(
                session, keys, node, q=view["q"], node_slug=view["cat"], date_from=_date(view["date_from"]),
                date_to=_date(view["date_to"]), refile_existing=refile,
                remember=form.get("remember") in ("1", "on", "true"),
                per_entry_merchants=form.get("per_entry") in ("1", "on", "true"), dry_run=not apply_now,
                by=getattr(request.state, "user_email", None))
            n, labels, ids = report.groups, keys, []
        else:
            ids = sorted({int(v) for v in form.getlist("merchant_ids") if str(v).isdigit()})
            if not ids:
                raise HTTPException(status_code=400, detail="Tick at least one merchant")
            report = assign_category(session, ids, node, refile_existing=refile, dry_run=not apply_now)
            n = report.merchants
            names = {m.id: m.canonical_name for m in session.exec(select(Merchant).where(Merchant.id.in_(ids))).all()}
            labels = [names[i] for i in ids if i in names]
    except ValueError:
        raise HTTPException(status_code=400, detail="Pick a sub-category to apply")
    if not apply_now:
        return templates.TemplateResponse(request, "transactions/bulk_preview.html", {
            "report": report, "n": n, "labels": labels, "ids": ids, "keys": labels if view["by"] == "provider" else [],
            "node_label": " \u203a ".join(reversed(_path_names(session, node))), "slug": slug, "refile": refile,
            "remember": form.get("remember") in ("1", "on", "true"), "per_entry": form.get("per_entry") in ("1", "on", "true"),
            "view": view, "view_qs": _view_qs({k: v for k, v in view.items() if k != "page"}),
        })
    moved = getattr(report, "moved_from_elsewhere", 0)
    filed = getattr(report, "filed_from_unsorted", 0)
    query = urlencode({**{k: v for k, v in view.items() if v not in ("", None)}, "applied": n, "moved": moved,
                       "filed": filed, "rules": getattr(report, "rules_saved", 0)})
    return RedirectResponse(f"/financials/transactions/bulk?{query}", status_code=303)


def _path_names(session: Session, node: CategoryNode) -> list[str]:
    names = [node.name]
    while node.parent_id:
        node = session.get(CategoryNode, node.parent_id)
        names.append(node.name)
    return names

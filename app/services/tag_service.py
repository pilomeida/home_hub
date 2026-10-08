"""Tags on category nodes: service layer for tag management and aggregation."""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select, func, and_

from app.models.tag import Tag, NodeTag
from app.models.category_node import CategoryNode
from app.models.transaction import Transaction, TransactionType
from app.services.tag_seed import TAG_LABELS, TAG_ASSIGNMENTS
from app.services.taxonomy import descendant_ids


@dataclass
class TagTotal:
    """Spending aggregated by tag across different time periods."""
    name: str
    label: str
    month_to_date: float
    year_to_date: float
    last_year: float
    previous_month: float
    transaction_count_ytd: int


def ensure_tags(session: Session) -> int:
    """Create missing Tag rows and NodeTag assignments. Idempotent.

    Expands node slugs to descendants and creates NodeTag rows for all matching nodes.
    Unknown slugs are silently skipped. Removes NodeTag rows no longer in TAG_ASSIGNMENTS.
    Returns number of changes (tags or node_tags created/removed).
    """
    changes = 0

    # Create missing Tag rows
    existing_tags = {t.name for t in session.exec(select(Tag)).all()}
    for name, label in TAG_LABELS.items():
        if name not in existing_tags:
            tag = Tag(name=name, label=label)
            session.add(tag)
            changes += 1
    session.commit()

    # Build the set of node_ids that should be tagged
    target_assignments: dict[str, set[int]] = {}  # tag_name -> set of node_ids
    for tag_name, slugs in TAG_ASSIGNMENTS.items():
        target_assignments[tag_name] = set()
        for slug in slugs:
            node = session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).first()
            if node is not None:
                # Include the node and all its descendants
                target_assignments[tag_name].update(descendant_ids(session, node.id))

    # Reconcile existing NodeTag rows with target assignments
    for tag in session.exec(select(Tag)).all():
        existing_node_ids = {nt.node_id for nt in session.exec(
            select(NodeTag).where(NodeTag.tag_id == tag.id)
        ).all()}
        target_node_ids = target_assignments.get(tag.name, set())

        # Remove NodeTag rows no longer in assignments
        to_remove = existing_node_ids - target_node_ids
        for node_id in to_remove:
            nt = session.exec(
                select(NodeTag).where(and_(NodeTag.tag_id == tag.id, NodeTag.node_id == node_id))
            ).first()
            if nt:
                session.delete(nt)
                changes += 1

        # Add missing NodeTag rows
        to_add = target_node_ids - existing_node_ids
        for node_id in to_add:
            nt = NodeTag(tag_id=tag.id, node_id=node_id)
            session.add(nt)
            changes += 1

    session.commit()
    return changes


def all_tags(session: Session) -> list[Tag]:
    """Return all tags ordered by label."""
    return session.exec(select(Tag).order_by(Tag.label)).all()


def tag_node_ids(session: Session, tag_name: str) -> list[int]:
    """Return all node ids (including descendants) tagged with the given tag."""
    tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
    if tag is None:
        return []
    return [nt.node_id for nt in session.exec(
        select(NodeTag).where(NodeTag.tag_id == tag.id)
    ).all()]


def node_tag_names(session: Session) -> dict[int, list[str]]:
    """Return a dict mapping node_id -> list of tag names, loaded in one query."""
    result: dict[int, list[str]] = {}
    for nt in session.exec(select(NodeTag)).all():
        tag = session.get(Tag, nt.tag_id)
        if nt.node_id not in result:
            result[nt.node_id] = []
        result[nt.node_id].append(tag.name)
    return result


def _month_key(d: date) -> str:
    """Return YYYY-MM key for a date."""
    return f"{d.year:04d}-{d.month:02d}"


def _month_start(d: date) -> date:
    """Return the first day of the month containing d."""
    return date(d.year, d.month, 1)


def tag_totals(session: Session, today: date) -> list[TagTotal]:
    """Aggregate spending by tag across different time periods.

    Only considers transactions with paid_date, filed under nodes with kind == "out",
    and not under "unsorted". Amount semantics: DEBIT = +amount, CREDIT = -amount.
    TRANSFER-type rows are ignored.
    """
    # Get all tags and their assigned nodes
    tags = all_tags(session)
    tag_to_nodes = {}
    for tag in tags:
        tag_to_nodes[tag.name] = set(tag_node_ids(session, tag.name))

    # Fetch all outgoing transactions with paid_date
    statement = select(Transaction).where(
        Transaction.paid_date.isnot(None),
        Transaction.transaction_type != TransactionType.TRANSFER,
    )
    transactions = session.exec(statement).all()

    # Build category_id -> kind mapping
    node_kinds = {}
    for node in session.exec(select(CategoryNode)).all():
        node_kinds[node.id] = node.kind

    # Aggregate by tag
    totals = []

    for tag in tags:
        tagged_node_ids = tag_to_nodes[tag.name]

        # Filter transactions: must be filed under a tagged node with kind="out"
        # and not under "unsorted"
        tag_transactions = [
            t for t in transactions
            if t.category_id and t.category_id in tagged_node_ids
            and node_kinds.get(t.category_id) == "out"
            and not (session.get(CategoryNode, t.category_id).slug.startswith("unsorted"))
        ]

        # Calculate periods
        month_key_today = _month_key(today)
        month_start_today = _month_start(today)
        year_start = date(today.year, 1, 1)
        prev_year_start = date(today.year - 1, 1, 1)
        prev_year_end = date(today.year - 1, 12, 31)

        # Previous month
        if today.month == 1:
            prev_month_start = date(today.year - 1, 12, 1)
            prev_month_end = date(today.year - 1, 12, 31)
        else:
            prev_month_start = date(today.year, today.month - 1, 1)
            if today.month == 2:
                prev_month_end = date(today.year, 1, 31)
            else:
                prev_month_end = date(today.year, today.month - 1, 28 if today.month - 1 != 2 else 28)
                # Correct end date for months
                import calendar
                prev_month_end = date(today.year, today.month - 1,
                                     calendar.monthrange(today.year, today.month - 1)[1])

        month_to_date = 0.0
        previous_month = 0.0
        year_to_date = 0.0
        last_year = 0.0
        transaction_count_ytd = 0
        seen_ytd = set()

        for t in tag_transactions:
            # Amount: DEBIT = +, CREDIT = -
            amount = t.amount if t.transaction_type == TransactionType.DEBIT else -t.amount

            # Month to date
            if _month_start(t.paid_date) == month_start_today and t.paid_date <= today:
                month_to_date += amount

            # Previous month
            if t.paid_date >= prev_month_start and t.paid_date <= prev_month_end:
                previous_month += amount

            # Year to date
            if t.paid_date >= year_start and t.paid_date <= today:
                year_to_date += amount
                if t.id not in seen_ytd:
                    seen_ytd.add(t.id)
                    transaction_count_ytd += 1

            # Last year (full calendar year)
            if t.paid_date >= prev_year_start and t.paid_date <= prev_year_end:
                last_year += amount

        totals.append(TagTotal(
            name=tag.name,
            label=tag.label,
            month_to_date=round(month_to_date, 2),
            year_to_date=round(year_to_date, 2),
            last_year=round(last_year, 2),
            previous_month=round(previous_month, 2),
            transaction_count_ytd=transaction_count_ytd,
        ))

    return totals

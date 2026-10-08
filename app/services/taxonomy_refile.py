"""One-off (re-runnable) backfill: file existing transactions into the category tree."""

import asyncio
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, auto_fits, file_transaction, get_node
from app.services.taxonomy_seed import LEGACY_TO_SLUG


@dataclass
class RefileReport:
    filed_from_merchant: int = 0
    filed_from_legacy: int = 0
    sent_to_review: int = 0
    already_filed: int = 0


def refile_all(session: Session, dry_run: bool = True) -> RefileReport:
    report = RefileReport()
    unsorted = get_node(session, UNSORTED_SLUG)
    # Pre-pass: a confirmed merchant with no node gets one from the MERCHANT's own
    # legacy value only (never from a transaction), so row order cannot matter.
    derived: dict[int, int] = {}
    for m in session.exec(select(Merchant).where(
            Merchant.confirmed == True, Merchant.default_category_id.is_(None))).all():  # noqa: E712
        slug = LEGACY_TO_SLUG.get(m.default_category)
        if slug:
            derived[m.id] = get_node(session, slug).id
            if not dry_run:
                m.default_category_id = derived[m.id]
                session.add(m)
    for t in session.exec(select(Transaction)).all():
        if t.category_id is not None:
            report.already_filed += 1
            continue
        merchant = session.get(Merchant, t.merchant_id) if t.merchant_id else None
        merchant_node_id = (merchant.default_category_id or derived.get(merchant.id)) if (
            merchant and merchant.confirmed) else None
        if merchant_node_id:
            node, bucket = session.get(CategoryNode, merchant_node_id), "filed_from_merchant"
        elif LEGACY_TO_SLUG.get(t.category):
            node, bucket = get_node(session, LEGACY_TO_SLUG[t.category]), "filed_from_legacy"
        else:
            node, bucket = unsorted, "sent_to_review"
        if not auto_fits(session, t, node):
            node, bucket = unsorted, "sent_to_review"
        setattr(report, bucket, getattr(report, bucket) + 1)
        if not dry_run:
            file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return report


PER_CALL_TIMEOUT = 120  # seconds per gateway call; a timeout counts as a failure
ABORT_AFTER_CONSECUTIVE_FAILURES = 20
PROGRESS_EVERY = 25
DEFAULT_CONCURRENCY, MAX_CONCURRENCY = 6, 12


def parse_concurrency(argv: list[str]) -> int:
    """`--concurrency N` / `--concurrency=N` from argv, clamped to 1..12 (default 6)."""
    value = None
    for i, arg in enumerate(argv):
        if arg == "--concurrency":
            if i + 1 >= len(argv):
                raise ValueError("--concurrency needs a number")
            value = argv[i + 1]
        elif arg.startswith("--concurrency="):
            value = arg.split("=", 1)[1]
    if value is None:
        return DEFAULT_CONCURRENCY
    try:
        return max(1, min(MAX_CONCURRENCY, int(value)))
    except ValueError:
        raise ValueError(f"--concurrency must be a whole number, got {value!r}") from None


@dataclass
class ReclassifyReport:
    merchants_asked: int = 0
    merchants_resolved: int = 0
    transactions_filed: int = 0
    still_unsorted: int = 0
    failed: int = 0
    aborted: bool = False
    abort_message: str = ""


@dataclass
class _WorkItem:
    merchant_id: int
    provider: str
    is_credit: bool
    count: int


_SKIPPED = object()


async def reclassify_unsorted(
    session: Session, gateway=None, concurrency: int = DEFAULT_CONCURRENCY, progress=None,
    best_guess: bool = False, only_undecided: bool = False,
) -> ReclassifyReport:
    """Ask the LLM once per distinct merchant that has Unsorted transactions and
    file them under the answer. The merchant stays confirmed=False so Pedro can
    still correct it on the Needs Review page. Commits per merchant.

    Only the gateway calls run concurrently (at most `concurrency` in flight);
    every database read and write happens in this task, one merchant per commit.
    With only_undecided the model is asked only about merchants with no default node.
    With best_guess the model may not answer "unsure" (a second pass for the
    merchants the first pass left behind). Merchants with the most Unsorted rows go first. A failed or timed-out call
    is counted and skipped; ABORT_AFTER_CONSECUTIVE_FAILURES in a row abort."""
    from app.llm_gateway import get_gateway
    from app.services.classification_engine import (
        MerchantResolutionError, ask_merchant_llm, candidate_slugs, legacy_category_for,
    )

    concurrency = max(1, min(MAX_CONCURRENCY, concurrency))
    report = ReclassifyReport()
    emit = progress or (lambda _line: None)
    unsorted = get_node(session, UNSORTED_SLUG)
    txns = session.exec(select(Transaction).where(
        Transaction.category_id == unsorted.id, Transaction.merchant_id.is_not(None))).all()
    by_merchant: dict[int, list[Transaction]] = {}
    for t in txns:
        by_merchant.setdefault(t.merchant_id, []).append(t)
    merchants = {m.id: m for m in session.exec(
        select(Merchant).where(Merchant.id.in_(list(by_merchant)))).all()} if by_merchant else {}

    # Prepare every work item up front, from data loaded once.
    legacy_items: list[tuple[int, str]] = []
    llm_items: list[_WorkItem] = []
    for merchant_id, group in by_merchant.items():
        merchant = merchants.get(merchant_id)
        if merchant is None:
            continue
        if merchant.confirmed and merchant.default_category_id is not None:
            continue  # Pedro's own decisions are never touched; rows stay Unsorted
        if only_undecided and merchant.default_category_id is not None:
            continue  # a merchant that already has a node is for reconcile, not for the model
        legacy_slug = LEGACY_TO_SLUG.get(merchant.default_category) if merchant.confirmed else None
        if legacy_slug:
            legacy_items.append((merchant_id, legacy_slug))
        else:
            llm_items.append(_WorkItem(
                merchant_id, group[0].provider,
                any(t.transaction_type == TransactionType.CREDIT for t in group), len(group)))
    order = lambda mid: (-len(by_merchant[mid]), merchants[mid].canonical_name or "", mid)  # noqa: E731
    legacy_items.sort(key=lambda x: order(x[0]))
    llm_items.sort(key=lambda w: order(w.merchant_id))
    total = len(legacy_items) + len(llm_items)
    done = 0

    def tick(final: bool = False) -> None:
        nonlocal done
        if not final:
            done += 1
        if final or done % PROGRESS_EVERY == 0:
            emit(f"reclassify: {done}/{total} merchants, resolved {report.merchants_resolved}, "
                 f"still unsorted {report.still_unsorted}, failed {report.failed}, "
                 f"transactions filed {report.transactions_filed}")

    def file_group(merchant_id: int, node, legacy_category=None) -> None:
        merchant = merchants[merchant_id]
        merchant.default_category_id = node.id
        if legacy_category is not None:
            merchant.default_category = legacy_category
        session.add(merchant)
        group = by_merchant[merchant_id]
        fitting = [t for t in group if auto_fits(session, t, node)]
        for t in fitting:
            file_transaction(session, t, node)
        report.merchants_resolved += 1
        report.transactions_filed += len(fitting)
        report.still_unsorted += len(group) - len(fitting)
        session.commit()

    for merchant_id, slug in legacy_items:  # no LLM: his legacy category is the decision
        file_group(merchant_id, get_node(session, slug))
        tick()

    if llm_items:
        slugs = candidate_slugs(session)
        gw = gateway or get_gateway()
        sem = asyncio.Semaphore(concurrency)
        stop = False
        streak = 0  # in-task failure streak, only used to stop launching calls

        async def call(item: _WorkItem):
            nonlocal stop, streak
            async with sem:
                if stop:
                    return item, _SKIPPED
                try:
                    out = await asyncio.wait_for(
                        ask_merchant_llm(item.provider, slugs, gateway=gw, is_credit=item.is_credit,
                                        best_guess=best_guess),
                        timeout=PER_CALL_TIMEOUT)
                    streak = 0
                    return item, out
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — any gateway/parse/timeout error is one failure
                    streak += 1
                    if streak >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                        stop = True
                    return item, exc

        tasks = [asyncio.ensure_future(call(item)) for item in llm_items]
        consecutive = 0
        try:
            for future in asyncio.as_completed(tasks):
                item, out = await future
                if out is _SKIPPED:
                    continue
                report.merchants_asked += 1
                if isinstance(out, BaseException):
                    report.failed += 1
                    consecutive += 1
                    tick()
                    if consecutive >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                        report.aborted = True
                        report.abort_message = (
                            f"reclassify aborted after {consecutive} consecutive failures "
                            f"(last: {out!s}); {total - done} merchants not processed")
                        break
                    continue
                consecutive = 0
                _name, node_slug, _nature = out
                if node_slug == UNSORTED_SLUG:
                    report.still_unsorted += len(by_merchant[item.merchant_id])
                else:
                    node = get_node(session, node_slug)
                    file_group(item.merchant_id, node, legacy_category_for(session, node))
                tick()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    if report.aborted:
        emit(report.abort_message)
    tick(final=True)
    return report

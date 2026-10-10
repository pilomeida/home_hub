"""Merchant resolution and transaction classification: normalizes a raw
provider string to a canonical Merchant (rules first, LLM fallback), and
detects recurring-commitment and debt candidates for human review."""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.document import Document
from app.models.debt import Debt
from app.models.position import DebtMatchRule
from app.services.debt_ledger import default_kind, link_transaction_as_entry
from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction, TransactionType
from app.services.loan_insurance import LOAN_INSURANCE_SLUGS
from app.services.merchant_merge import find_merchant_by_name, resolve_merchant
from app.services.merchant_rules import is_multi_purpose, keyword_match, keyword_node_slug
from app.services.provider_rules import find_rule_node, provider_fits
from app.services.merchant_relabel import ensure_structured_merchant
from app.services.structured_providers import structured_merchant
from app.services.taxonomy import UNSORTED_SLUG, auto_fits, default_node_id, direction_matches, file_transaction, legacy_category_for, get_node, leaf_slugs
from app.services.loan_insurance import link_insurance_transaction
from app.services.loan_linking import link_transaction_to_loan
from app.services.json_utils import strip_json_fences
from app.llm_gateway import CLASSIFICATION, get_gateway

log = logging.getLogger(__name__)

_LOCATION_WORDS = ("mafra", "ericeira")

_STORE_CODE_RE = re.compile(r"\s+\d+-\w+$")
_LOCATION_RE = re.compile(r"(\s+(?:" + "|".join(_LOCATION_WORDS) + r"))+$", re.IGNORECASE)


def normalize_provider(raw: str) -> str:
    """Strip statement-specific noise (trailing store codes, trailing
    known-location words) from a raw provider string, producing a
    normalized lookup key. Real examples this handles: "MODELO HIPER
    2640-MAFR" / "MODELO HIPER MAFRA" / "MODELO HIPER" all normalize to
    "modelo hiper"."""
    key = raw.strip().lower()
    key = _STORE_CODE_RE.sub("", key)
    key = _LOCATION_RE.sub("", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


_MERCHANT_SYSTEM_PROMPT = """You resolve a raw bank statement provider \
string to a canonical merchant identity and file it in the household's \
category tree. Respond with ONLY a JSON object, no prose, matching this \
shape exactly:

{
  "canonical_name": "string, the COUNTERPARTY: the institution, company or \
person on the other side, e.g. 'Modelo Hiper', 'Santander', 'Matias Almeida'. \
Never the product, fund, action or channel: a subscription to Santander's \
Aforro PPR fund is merchant 'Santander'; 'Transfer to Matias Almeida' is \
'Matias Almeida'. What it is for belongs in node_slug.",
  "node_slug": "the single best category slug from the list below",
  "nature": "essential or discretionary"
}

Choose the single best node_slug from this list (and nothing else):
{slugs}

{unsure_rule}
Credit-only slugs (money coming in; never for a debit): those starting with \
income., loans-debt.money-borrowed., loans-debt.money-lent-out.from-, \
savings-investments.earnings. or savings-investments.withdrawals.
Debit-only slugs (money going out; never for a credit): every other slug, \
including all loans-debt. slugs (loan repayments, loan interest and fees, \
money lent out)."""


_UNSURE_RULE = 'Use "{unsorted}" if you are unsure.'
_BEST_GUESS_RULE = """You must always choose the best-fitting slug; there is no "unsure" \
option. For a person-to-person transfer (a name after TRF, MBWay, "Transfer to"): \
a payment for work or services goes to the matching income (credit) or spending \
(debit) slug; money lent or borrowed goes to a loans-debt slug; otherwise pick \
the closest direction-valid slug."""


def _merchant_schema(slugs: list[str]) -> dict:
    """Response schema for merchant resolution; the parser indexes all three
    fields directly, so they stay required. node_slug is limited to `slugs`."""
    return {
        "type": "object",
        "required": ["canonical_name", "node_slug", "nature"],
        "properties": {
            "canonical_name": {"type": "string"},
            "node_slug": {"type": "string", "enum": slugs},
            "nature": {"type": "string", "enum": ["essential", "discretionary"]},
        },
    }


# Nodes only the loan-insurance linker may file under (rule / amount match); the
# LLM never sees or may choose them, or a generic insurer would count as loan insurance.
LLM_EXCLUDED_SLUGS = LOAN_INSURANCE_SLUGS


class MerchantResolutionError(Exception):
    pass


@dataclass
class ResolvedMerchant:
    canonical_name: str
    node_slug: str
    category: Category  # legacy value of the node, for consumers still on the old column
    nature: Nature


def candidate_slugs(session: Session) -> list[str]:
    """Leaf slugs the LLM may choose from (loan-insurance leaves excluded)."""
    return [s for s in leaf_slugs(session) if s not in LLM_EXCLUDED_SLUGS]


async def ask_merchant_llm(
    raw_provider: str, slugs: list[str], gateway=None, is_credit: Optional[bool] = None,
    best_guess: bool = False,
) -> tuple[str, str, Nature]:
    """The gateway half of merchant resolution: NO database access, so callers may
    run many of these concurrently. Returns (canonical_name, node_slug, nature)."""
    if not slugs:
        raise MerchantResolutionError("Category tree is not seeded (no leaf nodes)")
    gw = gateway or get_gateway()
    if best_guess:  # "unsure" is not an answer: the node is removed from the choices
        slugs = [s for s in slugs if s != UNSORTED_SLUG]
        if not slugs:
            raise MerchantResolutionError("No category nodes to choose from")
    schema = _merchant_schema(slugs)
    rule = _BEST_GUESS_RULE if best_guess else _UNSURE_RULE
    system = (_MERCHANT_SYSTEM_PROMPT.replace("{unsure_rule}", rule)
              .replace("{slugs}", "\n".join(slugs)).replace("{unsorted}", UNSORTED_SLUG))
    direction = "" if is_credit is None else (" (a credit)" if is_credit else " (a debit)")

    result = await gw.run(
        CLASSIFICATION,
        system,
        f"Resolve this provider string{direction} as JSON: {raw_provider!r}",
        response_schema=schema,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
        node_slug = data["node_slug"]
        if node_slug not in slugs:
            raise ValueError(f"unknown node_slug {node_slug!r}")
        return data["canonical_name"], node_slug, Nature(data["nature"])
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MerchantResolutionError(f"Could not resolve merchant: {exc}") from exc


async def resolve_merchant_via_llm(
    raw_provider: str, session: Session, gateway=None, is_credit: Optional[bool] = None
) -> ResolvedMerchant:
    """Ask the gateway to resolve a raw provider string to a canonical
    merchant name, category-tree leaf, and nature. Called only when the rules
    tier (normalize_provider + a Merchant lookup) finds no existing match.
    The session supplies the leaf slugs the model may choose from."""
    name, node_slug, nature = await ask_merchant_llm(
        raw_provider, candidate_slugs(session), gateway=gateway, is_credit=is_credit)
    return ResolvedMerchant(
        canonical_name=name,
        node_slug=node_slug,
        category=legacy_category_for(session, get_node(session, node_slug)),
        nature=nature,
    )


@dataclass
class ClassificationResult:
    merchant: Merchant
    created_new_merchant: bool


def _find_merchant_by_key(session: Session, normalized_key: str) -> Optional[Merchant]:
    statement = select(Merchant).where(Merchant.normalized_key == normalized_key)
    return resolve_merchant(session, session.exec(statement).first())  # a merged merchant answers as its survivor


async def classify_transaction(
    session: Session, transaction: Transaction, gateway=None
) -> ClassificationResult:
    """Resolve transaction.provider to a Merchant (rules tier, then LLM
    fallback on a miss), and set transaction.merchant_id/account_id/nature
    from the result. Does not commit — the caller controls the
    transaction boundary. This is the single entry point both the live
    ingestion pipeline and the historical backfill script use."""
    structured = structured_merchant(transaction.provider)
    if structured is not None:  # a loan instalment / loan insurance debit: one fixed merchant, no model call
        merchant, created_new_merchant = ensure_structured_merchant(session, structured)
        normalized_key = structured.key
    else:
        normalized_key = normalize_provider(transaction.provider)
        merchant = _find_merchant_by_key(session, normalized_key)
        created_new_merchant = False

    resolved_node_slug = None
    if merchant is None:
        keyword = keyword_match(transaction.provider)
        if keyword:  # a fixed ruling, no LLM call
            keyword_slug, chain_name = keyword
            keyword_node = get_node(session, keyword_slug)
            resolved = ResolvedMerchant(
                canonical_name=chain_name, node_slug=keyword_slug,
                category=legacy_category_for(session, keyword_node), nature=Nature.ESSENTIAL)
        else:
            resolved = await resolve_merchant_via_llm(
                transaction.provider, session, gateway=gateway,
                is_credit=transaction.transaction_type == TransactionType.CREDIT,
            )
        resolved_node_slug = resolved.node_slug
        existing = find_merchant_by_name(session, resolved.canonical_name)
        if existing is not None:
            # The counterparty is already known under another description: this text becomes an alias of
            # it, so one counterparty is one merchant however many descriptions the bank writes.
            session.add(Merchant(canonical_name=resolved.canonical_name, normalized_key=normalized_key,
                                 merged_into_id=existing.id))
            session.flush()
            merchant = existing
        else:
            merchant = Merchant(
                canonical_name=resolved.canonical_name,
                default_category=resolved.category,
                default_category_id=get_node(session, resolved.node_slug).id,
                default_nature=resolved.nature,
                normalized_key=normalized_key,
                by_provider=is_multi_purpose(resolved.canonical_name),
            )
            session.add(merchant)
            session.flush()
            created_new_merchant = True

    transaction.merchant_id = merchant.id
    if transaction.nature is None:
        transaction.nature = merchant.default_nature

    # A loan instalment links to its loan and is filed under Loans; that
    # filing wins over merchant memory (also on re-classification).
    loan_filed = False
    if transaction.debt_id is None:
        loan_filed = link_transaction_to_loan(session, transaction)
        if not loan_filed:  # insurance debits (SEG...) carry no loan number: rule or amount match
            try:
                loan_filed = link_insurance_transaction(session, transaction)
            except Exception:
                log.exception("loan insurance linking failed for transaction %s", transaction.id)
    elif transaction.category_id is not None:
        current = session.get(CategoryNode, transaction.category_id)
        loan_filed = current is not None and current.slug.startswith("loans-debt.")

    # Informal-loan rules (counterparty key -> debt) run after loan linking. The
    # transaction becomes a ledger entry with the role its direction gives; a
    # TRANSFER-type one has no reliable direction and stays in Needs Review.
    if transaction.debt_id is None:
        rule = session.exec(
            select(DebtMatchRule).where(DebtMatchRule.normalized_key == normalized_key)
        ).first()
        debt = session.get(Debt, rule.debt_id) if rule is not None else None
        if debt is not None and debt.external_number is None:
            kind = default_kind(debt.direction, transaction.transaction_type)
            if kind is not None:
                session.add(transaction)
                session.flush()
                link_transaction_as_entry(session, debt, transaction, kind)
                current = session.get(CategoryNode, transaction.category_id) if transaction.category_id else None
                loan_filed = loan_filed or (current is not None and current.slug.startswith("loans-debt"))

    # Merchant memory: a merchant with a tree node files every new transaction
    # under it (Unsorted included, so it lands in the Needs Review queue).
    # A merchant with no node (legacy rows) leaves the transaction unfiled.
    # A provider rule (Pedro: "transfers to Matias are family") beats the merchant's default, and a
    # channel merchant (by_provider) has no default to apply: its entries wait for a rule or for review.
    rule_filed = False
    if not loan_filed:
        rule_node = find_rule_node(session, transaction.provider)
        if rule_node is not None and provider_fits(session, transaction, rule_node):
            file_transaction(session, transaction, rule_node)
            rule_filed = True
    if not loan_filed and not rule_filed and merchant.by_provider and resolved_node_slug:
        # A multi-purpose counterparty has no category of its own: this description's category does.
        llm_node = get_node(session, resolved_node_slug)
        if auto_fits(session, transaction, llm_node):
            file_transaction(session, transaction, llm_node)
            rule_filed = True
    default_id = default_node_id(merchant, transaction)
    if not loan_filed and not rule_filed and not merchant.by_provider and default_id is not None:
        node = session.get(CategoryNode, default_id)
        if node is not None and auto_fits(session, transaction, node):
            file_transaction(session, transaction, node)

    if transaction.account_id is None:
        document = session.get(Document, transaction.document_id)
        if document is not None and document.account_id is not None:
            transaction.account_id = document.account_id

    session.add(transaction)
    return ClassificationResult(merchant=merchant, created_new_merchant=created_new_merchant)


def _is_consecutive_month(period_a: str, period_b: str) -> bool:
    year_a, month_a = (int(p) for p in period_a.split("-"))
    year_b, month_b = (int(p) for p in period_b.split("-"))
    return (year_b * 12 + month_b) - (year_a * 12 + month_a) == 1


def _has_recurring_run(transactions: list[Transaction], min_run: int = 3, tolerance: float = 0.10) -> bool:
    periods = sorted({t.statement_period for t in transactions if t.statement_period})
    if len(periods) < min_run:
        return False

    by_period: dict[str, list[float]] = {}
    for t in transactions:
        if t.statement_period:
            by_period.setdefault(t.statement_period, []).append(t.amount)

    consecutive = 1
    for i in range(1, len(periods)):
        consecutive = consecutive + 1 if _is_consecutive_month(periods[i - 1], periods[i]) else 1
        if consecutive >= min_run:
            run_periods = periods[i - consecutive + 1 : i + 1]
            run_amounts = [amt for p in run_periods for amt in by_period[p]]
            average = sum(run_amounts) / len(run_amounts)
            if average and all(abs(amt - average) / average <= tolerance for amt in run_amounts):
                return True
    return False


def detect_recurring_candidates(session: Session) -> list[Merchant]:
    """Merchants not yet reviewed for recurring status, whose transactions
    show 3+ consecutive statement_periods with every amount within +/-10%
    of that run's average."""
    merchants = session.exec(
        select(Merchant).where(Merchant.recurring_reviewed == False, Merchant.merged_into_id.is_(None))  # noqa: E712
    ).all()
    merchant_ids = [m.id for m in merchants]
    if not merchant_ids:
        return []

    all_transactions = session.exec(
        select(Transaction).where(Transaction.merchant_id.in_(merchant_ids))
    ).all()
    transactions_by_merchant: dict[int, list[Transaction]] = {}
    for t in all_transactions:
        transactions_by_merchant.setdefault(t.merchant_id, []).append(t)

    return [m for m in merchants if _has_recurring_run(transactions_by_merchant.get(m.id, []))]


_DEBT_TRANSFER_MARKER_RE = re.compile(r"P/\s*[A-ZÀ-Ú][A-ZÀ-Ú\s]+", re.IGNORECASE)
_DEBT_CANDIDATE_CATEGORIES = (Category.TRANSFER, Category.OTHER_EXPENSE)
_DEBT_CANDIDATE_MIN_AMOUNT = 500.0


def detect_debt_candidates(session: Session) -> list[Transaction]:
    """Transactions that look like a person-to-person transfer (a debt
    draw/repayment candidate): category TRANSFER or OTHER_EXPENSE, amount
    above the threshold, and provider text matching a Portuguese
    bank-transfer-to-a-named-individual pattern. Never auto-linked to a
    Debt — surfaced for a human decision only."""
    statement = (
        select(Transaction)
        .where(Transaction.category.in_(_DEBT_CANDIDATE_CATEGORIES))
        .where(Transaction.amount > _DEBT_CANDIDATE_MIN_AMOUNT)
        .where(Transaction.debt_id.is_(None))
        .where(Transaction.debt_candidate_reviewed.isnot(True))
    )
    transactions = session.exec(statement).all()
    return [t for t in transactions if _DEBT_TRANSFER_MARKER_RE.search(t.provider)]


@dataclass
class NeedsReviewQueue:
    unconfirmed_merchants: list[Merchant]
    recurring_candidates: list[Merchant]
    debt_candidates: list[Transaction]
    unclassified_transactions: list[Transaction]
    # Transactions with no tree node, or filed in Unsorted; grouped by merchant
    # (merchant_id -> transactions; transactions with no merchant are only listed flat).
    unsorted_transactions: list[Transaction] = field(default_factory=list)
    unsorted_by_merchant: dict[int, list[Transaction]] = field(default_factory=dict)
    unsorted_merchants: dict[int, Merchant] = field(default_factory=dict)


def unsorted_transactions_query():
    """Transactions with no node yet, or filed under the Unsorted group."""
    unsorted_ids = select(CategoryNode.id).where(CategoryNode.slug.like("unsorted%"))
    return select(Transaction).where(
        Transaction.category_id.is_(None) | Transaction.category_id.in_(unsorted_ids),
        Transaction.settled_by_id.is_(None),  # a document's record of a payment the bank already shows
    )


def get_needs_review_queue(session: Session) -> NeedsReviewQueue:
    """Everything currently needing a human decision: brand-new merchants
    not yet confirmed, recurring-payment candidates, debt candidates,
    transactions classify_transaction never managed to resolve to a
    Merchant at all (merchant_id left NULL), and transactions still unfiled
    or in Unsorted (grouped by merchant)."""
    unconfirmed = session.exec(
        select(Merchant).where(Merchant.confirmed == False, Merchant.merged_into_id.is_(None))  # noqa: E712
    ).all()
    unclassified = session.exec(
        select(Transaction).where(Transaction.merchant_id.is_(None), Transaction.settled_by_id.is_(None))
    ).all()
    unsorted = session.exec(unsorted_transactions_query().order_by(Transaction.id)).all()
    by_merchant: dict[int, list[Transaction]] = {}
    for t in unsorted:
        if t.merchant_id is not None:
            by_merchant.setdefault(t.merchant_id, []).append(t)
    merchants = {
        m.id: m for m in session.exec(select(Merchant).where(Merchant.id.in_(list(by_merchant)))).all()
    } if by_merchant else {}
    return NeedsReviewQueue(
        unconfirmed_merchants=unconfirmed,
        recurring_candidates=detect_recurring_candidates(session),
        debt_candidates=detect_debt_candidates(session),
        unclassified_transactions=unclassified,
        unsorted_transactions=unsorted,
        unsorted_by_merchant=by_merchant,
        unsorted_merchants=merchants,
    )


REVIEW_PAGE_SIZE = 50
REVIEW_SECTIONS = ("m", "r", "d", "c", "u")  # new merchants, recurring, debt, unclassified, unsorted


@dataclass
class ReviewSection:
    """Where one Needs Review list stands: which slice is shown out of how many."""
    total: int = 0
    offset: int = 0
    shown: int = 0

    @property
    def first(self) -> int:
        return self.offset + 1 if self.shown else 0

    @property
    def last(self) -> int:
        return self.offset + self.shown

    @property
    def next_offset(self) -> Optional[int]:
        return self.offset + REVIEW_PAGE_SIZE if self.offset + REVIEW_PAGE_SIZE < self.total else None

    @property
    def prev_offset(self) -> Optional[int]:
        return max(self.offset - REVIEW_PAGE_SIZE, 0) if self.offset > 0 else None


@dataclass
class NeedsReviewPage:
    queue: NeedsReviewQueue
    sections: dict[str, ReviewSection]


def _clamp_offset(offset: int, total: int) -> int:
    """A request past the end (rows were confirmed meanwhile) lands on the last page."""
    offset = max(int(offset or 0), 0)
    if offset >= total:
        offset = ((total - 1) // REVIEW_PAGE_SIZE) * REVIEW_PAGE_SIZE if total else 0
    return offset


def get_needs_review_page(
    session: Session, offsets: Optional[dict[str, int]] = None, limit: int = REVIEW_PAGE_SIZE
) -> NeedsReviewPage:
    """The Needs Review queue cut into pages of at most `limit` rows per list.
    Merchants and groups come busiest first (most transactions, then name).
    `offsets` is keyed by section: m, r, d, c, u. Same contents as
    get_needs_review_queue, just sliced."""
    offsets = offsets or {}
    queue = get_needs_review_queue(session)
    counts: dict[int, int] = {}
    for mid, n in session.exec(
        select(Transaction.merchant_id, func.count(Transaction.id)).group_by(Transaction.merchant_id)
    ).all():
        if mid is not None:
            counts[mid] = n

    def cut(key: str, items: list) -> tuple[list, ReviewSection]:
        off = _clamp_offset(offsets.get(key, 0), len(items))
        page = items[off:off + limit]
        return page, ReviewSection(total=len(items), offset=off, shown=len(page))

    new_merchants = sorted(queue.unconfirmed_merchants,
                           key=lambda m: (-counts.get(m.id, 0), m.canonical_name.lower(), m.id))
    new_page, m_sec = cut("m", new_merchants)
    rec_page, r_sec = cut("r", queue.recurring_candidates)
    debt_page, d_sec = cut("d", queue.debt_candidates)
    unc_page, c_sec = cut("c", queue.unclassified_transactions)

    def group_key(item):
        mid, txns = item
        m = queue.unsorted_merchants.get(mid)
        return (-len(txns), (m.canonical_name.lower() if m else ""), mid)

    groups = sorted(queue.unsorted_by_merchant.items(), key=group_key)
    group_page, u_sec = cut("u", groups)
    page_ids = {mid for mid, _ in group_page}

    return NeedsReviewPage(
        queue=NeedsReviewQueue(
            unconfirmed_merchants=new_page,
            recurring_candidates=rec_page,
            debt_candidates=debt_page,
            unclassified_transactions=unc_page,
            unsorted_transactions=queue.unsorted_transactions,
            unsorted_by_merchant=dict(group_page),
            unsorted_merchants={k: v for k, v in queue.unsorted_merchants.items() if k in page_ids},
        ),
        sections={"m": m_sec, "r": r_sec, "d": d_sec, "c": c_sec, "u": u_sec},
    )

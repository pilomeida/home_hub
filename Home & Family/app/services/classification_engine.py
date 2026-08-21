"""Merchant resolution and transaction classification: normalizes a raw
provider string to a canonical Merchant (rules first, LLM fallback), and
detects recurring-commitment and debt candidates for human review."""

import json
import re
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.config import settings
from app.models.document import Document
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction
from app.services.json_utils import strip_json_fences

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


_MERCHANT_MODEL = "claude-haiku-4-5-20251001"

_MERCHANT_SYSTEM_PROMPT = """You resolve a raw bank statement provider \
string to a canonical merchant identity. Respond with ONLY a JSON object, \
no prose, matching this shape exactly:

{
  "canonical_name": "string, a clean human-readable merchant name, e.g. \
'Modelo Hiper'",
  "category": "one of: electricity, water, gas, telecom, insurance, \
subscriptions, groceries, health, home, income, transfer, atm_withdrawal, \
restaurants, shopping, other_expense, other",
  "nature": "essential or discretionary"
}"""


class MerchantResolutionError(Exception):
    pass


@dataclass
class ResolvedMerchant:
    canonical_name: str
    category: Category
    nature: Nature


async def resolve_merchant_via_llm(
    raw_provider: str, client: Optional[AsyncAnthropic] = None
) -> ResolvedMerchant:
    """Ask Claude to resolve a raw provider string to a canonical merchant
    name, category, and nature. Called only when the rules tier
    (normalize_provider + a Merchant lookup) finds no existing match."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    message = await anthropic_client.messages.create(
        model=_MERCHANT_MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=_MERCHANT_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Resolve this provider string as JSON: {raw_provider!r}"}
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ResolvedMerchant(
            canonical_name=data["canonical_name"],
            category=Category(data["category"]),
            nature=Nature(data["nature"]),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MerchantResolutionError(f"Could not resolve merchant: {exc}") from exc


@dataclass
class ClassificationResult:
    merchant: Merchant
    created_new_merchant: bool


def _find_merchant_by_key(session: Session, normalized_key: str) -> Optional[Merchant]:
    statement = select(Merchant).where(Merchant.normalized_key == normalized_key)
    return session.exec(statement).first()


async def classify_transaction(
    session: Session, transaction: Transaction, client: Optional[AsyncAnthropic] = None
) -> ClassificationResult:
    """Resolve transaction.provider to a Merchant (rules tier, then LLM
    fallback on a miss), and set transaction.merchant_id/account_id/nature
    from the result. Does not commit — the caller controls the
    transaction boundary. This is the single entry point both the live
    ingestion pipeline and the historical backfill script use."""
    normalized_key = normalize_provider(transaction.provider)
    merchant = _find_merchant_by_key(session, normalized_key)
    created_new_merchant = False

    if merchant is None:
        resolved = await resolve_merchant_via_llm(transaction.provider, client=client)
        merchant = Merchant(
            canonical_name=resolved.canonical_name,
            default_category=resolved.category,
            default_nature=resolved.nature,
            normalized_key=normalized_key,
        )
        session.add(merchant)
        session.flush()
        created_new_merchant = True

    transaction.merchant_id = merchant.id
    transaction.nature = merchant.default_nature

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
        select(Merchant).where(Merchant.recurring_reviewed == False)  # noqa: E712
    ).all()
    candidates = []
    for merchant in merchants:
        transactions = session.exec(
            select(Transaction).where(Transaction.merchant_id == merchant.id)
        ).all()
        if _has_recurring_run(transactions):
            candidates.append(merchant)
    return candidates


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

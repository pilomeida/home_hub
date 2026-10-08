"""Bank-structured provider strings that are not merchants.

`COB.REC.31.<loan account>/<n>` is the bank collecting a loan instalment; `SEG:LAR|EDF|VIDA ...` is
the loan's insurance debit. Their text carries counters and dates, so left to the generic path every
instalment became a new merchant and a new model guess (Caixa Geral de Depósitos, Banco do Brasil,
EDF the electricity company, ...). They map to one fixed merchant each, with no model call."""

import re
from dataclasses import dataclass
from typing import Optional

from app.services.loan_insurance import BUILDING_SLUG, LIFE_SLUG, insurance_key, key_component

MORTGAGE_SLUG = "loans-debt.loan-repayments.mortgage"
_LOAN_COLLECTION = re.compile(r"^\s*COB\.?\s?REC\.?\s?31\.\d{9,}", re.IGNORECASE)


@dataclass(frozen=True)
class StructuredMerchant:
    key: str        # Merchant.normalized_key
    name: str
    node_slug: str


LENDER = StructuredMerchant("structured:credito-habitacao", "Santander – Crédito habitação", MORTGAGE_SLUG)
_INSURANCE = {
    "seg vida": StructuredMerchant("structured:seguro-vida-habitacao", "Seguro de vida (crédito habitação)", LIFE_SLUG),
    "seg edf": StructuredMerchant("structured:seguro-edificio-habitacao", "Seguro de edifício (crédito habitação)", BUILDING_SLUG),
    "seg lar": StructuredMerchant("structured:seguro-multirriscos-habitacao", "Seguro multirriscos (crédito habitação)", BUILDING_SLUG),
}
ALL = (LENDER, *_INSURANCE.values())


def structured_merchant(provider: Optional[str]) -> Optional[StructuredMerchant]:
    text = provider or ""
    if _LOAN_COLLECTION.match(text):
        return LENDER
    key = insurance_key(text)
    if key is not None and key_component(key) is not None:
        for prefix, merchant in _INSURANCE.items():
            if key == prefix or key.startswith(prefix + " "):
                return merchant
    return None

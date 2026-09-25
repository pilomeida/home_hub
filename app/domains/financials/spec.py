"""Financials' DomainSpec."""

from sqlmodel import Session, select

from app.domains.base import (
    CategorySpec, DomainSpec, FieldKind, FieldSpec, NavLink, WikiSchema,
)
from app.domains.financials.categories import FinancialsCategory
from app.domains.financials.handler import FinancialsHandler
from app.domains.financials.overview import financials_overview_card
from app.models.account import Account
from app.models.domain import Domain


def _account_choices(session: Session) -> list[tuple[str, str]]:
    accounts = session.exec(select(Account).order_by(Account.name)).all()
    return [(str(a.id), a.name) for a in accounts]


SPEC = DomainSpec(
    domain=Domain.FINANCIALS,
    label="Financials",
    description="Money: bills and invoices to pay, and bank or card account statements.",
    home_url="/financials/bills",
    categories=(
        CategorySpec(
            FinancialsCategory.BILL.value, "Bill / invoice",
            "A single bill, invoice, receipt or premium notice from one provider with one amount due.",
        ),
        CategorySpec(
            FinancialsCategory.STATEMENT.value, "Bank statement",
            "A bank or card account statement listing many transactions.",
        ),
    ),
    fields=(
        FieldSpec(
            "account_id", "Account", FieldKind.CHOICE, choices_provider=_account_choices,
            help="Which bank account a statement belongs to (optional).",
        ),
    ),
    handler=FinancialsHandler(),
    nav_links=(
        NavLink("Bills & Bank", "/financials/bills"),
        NavLink("Transactions", "/financials/transactions"),
        NavLink("Utilities", "/financials/utilities/electricity"),
    ),
    overview_card=financials_overview_card,
    document_url=lambda document: f"/financials/bills/{document.id}",
    wiki=WikiSchema(
        guidance=(
            "Financials covers bills, bank statements and the household's service providers. "
            "Record standing facts such as the current provider for a service, contract or policy "
            "numbers, tariffs, and renewal dates."
        ),
    ),
    infers_category=True,
)
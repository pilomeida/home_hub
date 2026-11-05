"""Financials' CategorySpec."""

from sqlmodel import Session, select

from app.categories.base import (
    CategorySpec, FieldKind, FieldSpec, FiledAs, KindSpec, NavLink, WikiSchema,
)
from app.categories.financials.ask import FINANCIALS_ASK_TOOLS
from app.categories.financials.kinds import FinancialsKind
from app.categories.financials.handler import (
    FinancialsHandler, read_bill, read_loan_history, read_statement, read_statement_positions,
)
from app.categories.financials.overview import financials_overview_card
from app.models.account import Account
from app.models.category import Category
from app.privacy.tiers import Tier


def _account_choices(session: Session) -> list[tuple[str, str]]:
    accounts = session.exec(select(Account).order_by(Account.name)).all()
    return [(str(a.id), a.name) for a in accounts]


SPEC = CategorySpec(
    category=Category.FINANCIALS,
    label="Financials",
    description="Money: bills and invoices to pay, and bank or card account statements.",
    home_url="/financials/bills",
    kinds=(
        KindSpec(
            FinancialsKind.BILL.value, "Bill / invoice",
            "A single bill, invoice, receipt or premium notice from one provider with one amount due.",
            suggested_tier=Tier.MID, reader=read_bill, files_as=frozenset({FiledAs.MONEY, FiledAs.TODOS, FiledAs.WIKI}),
        ),
        KindSpec(
            FinancialsKind.STATEMENT.value, "Bank statement",
            "A bank or card account statement listing many transactions.",
            suggested_tier=Tier.MID, reader=read_statement, files_as=frozenset({FiledAs.MONEY}),
        ),
        KindSpec(
            FinancialsKind.POSITIONS.value, "Statement – loans & savings positions only",
            "A consolidated bank statement read only for loan, fund and balance positions (never for transactions).",
            classifiable=False,
            suggested_tier=Tier.MID, reader=read_statement_positions, files_as=frozenset({FiledAs.MONEY, FiledAs.LOANS}),
        ),
        KindSpec(
            FinancialsKind.LOAN_HISTORY.value, "Online-banking loan movements printout",
            "An online-banking printout of one loan's instalment movements.",
            classifiable=False,
            suggested_tier=Tier.MID, reader=read_loan_history, files_as=frozenset({FiledAs.LOANS}),
        ),
    ),
    fields=(
        FieldSpec(
            "account_id", "Account", FieldKind.CHOICE, choices_provider=_account_choices,
            help="Which bank account a statement belongs to (optional).",
        ),
        FieldSpec(
            "policy_number", "Insurance policy number", FieldKind.TEXT,
            kinds=frozenset({FinancialsKind.BILL.value}),
            help="For an insurance premium: the policy it pays. The bill is then shown on that policy in Insurance.",
        ),
    ),
    handler=FinancialsHandler(),
    nav_links=(
        NavLink("Bills & Bank", "/financials/bills"),
        NavLink("Transactions", "/financials/transactions"),
        NavLink("Coming up", "/financials/coming-up"),
        NavLink("Bank connections", "/financials/bank/"),
        NavLink("Loans & Savings", "/financials/loans"),
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
    infers_kind=True,
    ask_tools=FINANCIALS_ASK_TOOLS,
)
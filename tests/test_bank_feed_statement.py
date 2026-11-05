"""Work-list B5: a bank line has no privacy level of its own; it takes its statement's. A line from the bank feed
hangs on its account's feed statement (app/categories/financials/feed.py), so every line has a statement."""

import ast
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import select

from app.categories.fields import file_url
from app.categories.financials.feed import feed_statement, is_feed_statement
from app.models.account import Account, AccountType
from app.models.category import Category
from app.models.transaction import Transaction, TransactionType
from app.privacy import settings as ps
from app.services.ingestion import Classification, RefileRefusedError, refile_document
from tests.knowledge_factories import make_document


def _account(session):
    account = Account(name="Conta à ordem", institution="Santander Totta", currency="EUR",
                      account_type=AccountType.CHECKING)
    session.add(account); session.commit(); session.refresh(account)
    return account


def _line(session, document, description):
    t = Transaction(document_id=document.id, description=description, transaction_type=TransactionType.DEBIT,
                    amount=12.5, currency="EUR", paid_date=date(2026, 10, 3))
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_each_account_has_one_feed_statement_and_it_is_a_statement(session):
    account = _account(session)
    first = feed_statement(session, account.id, "Santander Totta")
    again = feed_statement(session, account.id, "Santander Totta")
    assert first.id == again.id and is_feed_statement(first)
    assert (first.category, first.paper_kind) == (Category.FINANCIALS, "statement")
    assert not is_feed_statement(make_document(session, category=Category.FINANCIALS, paper_kind="statement"))


def test_every_bank_line_takes_the_level_of_its_statement(session):
    """A feed line, a line from a paper statement and a bill line: each one's level is its paper's."""
    account = _account(session)
    feed = feed_statement(session, account.id, "Santander Totta"); session.commit()
    paper = make_document(session, category=Category.FINANCIALS, paper_kind="statement", filename="2026-09.pdf")
    bill = make_document(session, category=Category.FINANCIALS, paper_kind="bill", filename="edp.pdf")
    lines = [_line(session, d, d.filename) for d in (feed, paper, bill)]
    ps.save_type(session, "financials", "statement", "house_mid", False, "pedro")
    ps.save_type(session, "financials", "bill", "house_low", False, "pedro")

    def level(t):
        document = session.get(type(feed), t.document_id)
        return ps.configured_tier(session, document.category, document.paper_kind)

    assert [level(t) for t in lines] == ["house_mid", "house_mid", "house_low"]


def test_every_place_that_makes_a_bank_line_hangs_it_on_a_paper():
    """No Transaction(...) anywhere in the app or the scripts is made without its statement (document_id=)."""
    missing = []
    for path in [*Path("app").rglob("*.py"), *Path("scripts").rglob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Transaction":
                if not any(k.arg == "document_id" for k in node.keywords) and not any(k.arg is None for k in node.keywords):
                    missing.append(f"{path}:{node.lineno}")
    assert not missing, f"bank lines made without their statement: {missing}"


@pytest.mark.asyncio
async def test_the_feed_statement_is_never_re_filed(session):
    account = _account(session)
    feed = feed_statement(session, account.id, "Santander Totta"); session.commit()
    with pytest.raises(RefileRefusedError, match="bank feed's statement"):
        await refile_document(session, feed, Classification(category=Category.FINANCIALS, paper_kind="bill", fields={}))
    assert (feed.category, feed.paper_kind) == (Category.FINANCIALS, "statement")


def test_a_feed_line_shows_that_it_came_from_the_bank_not_a_broken_file_link(client, session):
    from app.routers.to_check import line_card
    account = _account(session)
    feed = feed_statement(session, account.id, "Santander Totta"); session.commit()
    line = _line(session, feed, "GALP MAFRA")
    assert file_url(feed) is None
    card = line_card(session, line)
    assert "From the bank feed" in card and "/static/documents/" not in card
    page = client.get(f"/financials/bills/{feed.id}", headers={"Accept": "text/html"}).text
    assert "bank feed's statement" in page and f"/documents/{feed.id}/edit" not in page

"""PLAN step 10e: Waiting for you, new look. The grouped list; one item at a time (the paper left, what the Hub read
right, keyboard shortcuts); merchants as a table, the best guess filled in, confirmed in one go. The answers and what
they do are step 3's, unchanged."""

import importlib.util
from pathlib import Path

from sqlmodel import Session, select

from app.models.merchant import Merchant
from app.models.review import ReviewItem
from app.models.transaction import Transaction, TransactionType
from app.services import queue_view, review
from app.services.taxonomy import ensure_taxonomy, get_node
from tests.test_overview_service import _doc
from tests.test_review import _doubtful

SUPER = "food.groceries.supermarket"


def test_the_see_it_link_of_an_ai_answer_opens_a_real_page(client, session, levels_not_binding):
    _doubtful(session)
    [item] = review.waiting(session)
    assert item.evidence_url.startswith("/financials/transactions?q=")
    assert client.get(item.evidence_url).status_code == 200


def test_the_migration_repairs_the_links_already_stored(engine, session):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    session.add(ReviewItem(area="money", kind="ai_rule", ref="rule:1", title="t", why="w",
                           evidence_url="/transactions?q=Hipay"))
    session.add(ReviewItem(area="health", kind="health_area", ref="x:1", title="t", why="w", evidence_url="/health/areas"))
    session.commit()
    path = next(Path("alembic/versions").glob("b1d4f6a8c235_*.py"))
    spec = importlib.util.spec_from_file_location("m", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        module.upgrade()
    session.expire_all()
    urls = sorted(i.evidence_url for i in session.exec(select(ReviewItem)))
    assert urls == ["/financials/transactions?q=Hipay", "/health/areas"]


def _merchants(session, n_known=2):
    ensure_taxonomy(session)
    leaf = get_node(session, SUPER)
    doc = _doc(session)
    out = []
    for i in range(n_known):
        m = Merchant(canonical_name=f"Loja {i}", normalized_key=f"loja {i}", default_category_id=leaf.id)
        session.add(m); session.commit()
        session.add(Transaction(document_id=doc.id, description=f"LOJA {i}", amount=5.0,
                                transaction_type=TransactionType.DEBIT, merchant_id=m.id))
        out.append(m)
    unknown = Merchant(canonical_name="Quem", normalized_key="quem")
    session.add(unknown); session.commit()
    return out, unknown


def test_the_list_opens_with_one_row_per_area_and_the_merchants_apart(client, session, engine, levels_not_binding,
                                                                       monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)
    _doubtful(session)
    _merchants(session)
    session.commit()
    groups = queue_view.groups(session)
    assert [(g.label, g.count, g.button) for g in groups] == [("Money", 1, "Open the first"),
                                                             ("Merchants", 3, "Open the table")]
    html = client.get("/waiting").text
    assert 'href="/waiting/one?area=money"' in html and 'href="/waiting?area=merchants"' in html
    assert html.count('class="card witem"') == 1                 # the whole list is still there, merchants apart


def test_one_at_a_time_answers_moves_along_and_stops_at_the_end(client, session, levels_not_binding):
    _doubtful(session)
    session.commit()
    page = client.get("/waiting/one?area=money").text
    assert "1 of 1" in page and 'data-key="first"' in page and "⏎ the first answer" in page
    [item] = review.waiting(session, "money")
    assert "isn't about a single paper" in page                  # an AI answer has no one paper to show
    later = client.post(f"/waiting/one/{item.id}?area=money&n=0", data={"answer": "snooze"}).text
    assert "Nothing is waiting for you in Money." in later
    session.expire_all()
    assert session.get(ReviewItem, item.id).status == "snoozed"


def test_moving_along_records_no_answer(client, session, levels_not_binding):
    _doubtful(session)
    session.commit()
    [item] = review.waiting(session, "money")
    html = client.post(f"/waiting/one/{item.id}?area=money&n=0", data={"answer": "next"}).text
    assert "1 of 1" in html and "problem" not in html
    session.expire_all()
    assert session.get(ReviewItem, item.id).status == "waiting"


def test_a_line_to_check_shows_its_statement(session):
    doc = _doc(session, "santander-2026-10")
    t = Transaction(document_id=doc.id, description="X", amount=1.0, transaction_type=TransactionType.DEBIT)
    session.add(t); session.commit()
    item = ReviewItem(area="money", kind="to_check_line", ref=f"txn:{t.id}", title="t", why="w")
    assert queue_view.document_of(session, item).id == doc.id
    assert queue_view.document_of(session, ReviewItem(area="money", kind="statement_mismatch", ref=f"doc:{doc.id}",
                                                      title="t", why="w")).id == doc.id
    assert queue_view.document_of(session, ReviewItem(area="money", kind="ai_rule", ref="rule:1", title="t",
                                                      why="w")) is None


def test_merchants_are_a_table_with_the_guess_and_confirm_in_one_go(client, session, engine, monkeypatch):
    monkeypatch.setattr("app.db.engine", engine)
    known, unknown = _merchants(session)
    review.sync(session)
    html = client.get("/waiting?area=merchants").text
    assert "Food › Groceries › Supermarket" in html and "no guess: choose one" in html
    assert html.count('name="item"') == 2                        # only a merchant with a guess can be ticked
    items = {i.ref: i.id for i in review.waiting(session, merchants=True)}
    ticked = [items[f"merchant:{m.id}"] for m in known]
    done = client.post("/waiting/bulk", data={"item": ticked, "remember": "1"}).text
    assert "Confirmed 2 merchants." in done
    session.expire_all()
    assert all(session.get(Merchant, m.id).confirmed for m in known) and not session.get(Merchant, unknown.id).confirmed
    nothing = client.post("/waiting/bulk", data={}).text
    assert "Nothing was confirmed." in nothing and "No merchant was ticked." in nothing

import os

os.environ.setdefault("LLMSEL_URL", "http://gw.test:8010")
os.environ.setdefault("LLMSEL_TOKEN", "test-token")

import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture(autouse=True)
def pinned_today_for_the_stale_bill_rule(monkeypatch):
    """Tests use fixed 2026 due dates; the "no Pay to-do for a long-overdue bill" rule must not depend on the day the
    suite runs. The rule's own tests choose their date."""
    from datetime import date
    monkeypatch.setattr("app.services.todo_engine._today", lambda: date(2026, 9, 1))


_EMPTY = create_engine("sqlite://")       # no paper_types table: only the built-in areas


@pytest.fixture(autouse=True)
def added_types_from_the_test_database(monkeypatch):
    """The registry reads added areas and kinds (PLAN step 12a) from the test's own database (set by `engine`), never
    from the developer's data/ folder; with no test database, only the built-in areas exist."""
    from app.categories import registry
    monkeypatch.setattr(registry, "session_factory", lambda: Session(_EMPTY))
    monkeypatch.setattr(registry, "RECHECK_SECONDS", 3600.0)   # re-read only when told (forget_types)
    registry.forget_types()
    yield
    registry.forget_types()


@pytest.fixture()
def levels_not_binding():
    """For a unit test that calls a reader or the gateway directly: production always makes those calls inside a
    privacy scope (app/privacy/scope.py), and with levels not binding a scope lets them through untiered. Outside
    every scope the gateway refuses the call (finding F7), so a router or end-to-end test must never need this."""
    from app.privacy import scope
    token = scope._open.set(True)
    yield
    scope._open.reset(token)


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    from app.db import register_foreign_keys_pragma
    register_foreign_keys_pragma(test_engine)       # links enforced, like production (PLAN step 9, D7)
    from app import models  # noqa: F401 — registers tables on SQLModel.metadata

    SQLModel.metadata.create_all(test_engine)
    from app.categories import registry
    registry.session_factory = lambda: Session(test_engine)    # put back by the autouse fixture's monkeypatch
    registry.forget_types()
    return test_engine


@pytest.fixture()
def fk_session(tmp_path):
    """A session whose engine ENFORCES foreign keys, like production (app/db.py).
    Opt-in: only tests that ask for it run with FKs on."""
    from app.db import register_foreign_keys_pragma

    fk_engine = create_engine(
        f"sqlite:///{tmp_path / 'fk.db'}", connect_args={"check_same_thread": False}
    )
    register_foreign_keys_pragma(fk_engine)
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(fk_engine)
    with Session(fk_engine) as s:
        yield s


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture()
def payee_rules(session):
    """Pedro's payee rules (entities, payment texts, supermarket chains) as live has them since PLAN step 9 D8,
    written by the production seed over the seeded tree. Returns the session."""
    from app.services.payment_entities import seed
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    seed(session)
    session.commit()
    return session


@pytest.fixture()
def client(engine, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    from app.db import get_session_factory
    app.dependency_overrides[get_session_factory] = lambda: (lambda: Session(engine))
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def fake_category(monkeypatch):
    """Replace the registry with a single fake domain (registered under
    Category.HOUSE) so shared code can be tested against the contract alone."""
    from app.categories import registry
    from tests.category_fakes import make_fake_spec

    spec = make_fake_spec()
    monkeypatch.setattr(registry, "_specs_cache", {spec.category: spec})
    return spec


@pytest.fixture()
def two_categories(monkeypatch):
    """Registry replaced by two fake domains, so Plan B code is tested against
    the contract only: 'Money' (Category.FINANCIALS, infers its category, no
    fields) and Plan A's fake House-like spec (Category.HOUSE)."""
    import dataclasses

    from app.categories import registry
    from app.categories.base import KindSpec, WikiSchema
    from app.models.category import Category
    from tests.category_fakes import make_fake_spec

    house = make_fake_spec()
    money = dataclasses.replace(
        make_fake_spec(category=Category.FINANCIALS),
        label="Money", description="Money documents.", home_url="/money",
        kinds=(KindSpec("bill", "Bill", "A single bill."),
                    KindSpec("statement", "Bank statement", "A list of transactions.")),
        fields=(), wiki=WikiSchema(), infers_kind=True,
        document_url=lambda document: f"/money/{document.id}",
    )
    monkeypatch.setattr(registry, "_specs_cache", {money.category: money, house.category: house})
    return {"money": money, "house": house}


@pytest.fixture()
def inbox_documents_dir(tmp_path, monkeypatch):
    target = tmp_path / "documents"
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", target)
    return target

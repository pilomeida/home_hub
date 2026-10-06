import os

os.environ.setdefault("LLMSEL_URL", "http://gw.test:8010")
os.environ.setdefault("LLMSEL_TOKEN", "test-token")

import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture()
def engine(tmp_path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    from app import models  # noqa: F401 — registers tables on SQLModel.metadata

    SQLModel.metadata.create_all(test_engine)
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
def fake_domain(monkeypatch):
    """Replace the registry with a single fake domain (registered under
    Domain.HOUSE) so shared code can be tested against the contract alone."""
    from app.domains import registry
    from tests.domain_fakes import make_fake_spec

    spec = make_fake_spec()
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    return spec


@pytest.fixture()
def two_domains(monkeypatch):
    """Registry replaced by two fake domains, so Plan B code is tested against
    the contract only: 'Money' (Domain.FINANCIALS, infers its category, no
    fields) and Plan A's fake House-like spec (Domain.HOUSE)."""
    import dataclasses

    from app.domains import registry
    from app.domains.base import CategorySpec, WikiSchema
    from app.models.domain import Domain
    from tests.domain_fakes import make_fake_spec

    house = make_fake_spec()
    money = dataclasses.replace(
        make_fake_spec(domain=Domain.FINANCIALS),
        label="Money", description="Money documents.", home_url="/money",
        categories=(CategorySpec("bill", "Bill", "A single bill."),
                    CategorySpec("statement", "Bank statement", "A list of transactions.")),
        fields=(), wiki=WikiSchema(), infers_category=True,
        document_url=lambda document: f"/money/{document.id}",
    )
    monkeypatch.setattr(registry, "_specs_cache", {money.domain: money, house.domain: house})
    return {"money": money, "house": house}


@pytest.fixture()
def inbox_documents_dir(tmp_path, monkeypatch):
    target = tmp_path / "documents"
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", target)
    return target

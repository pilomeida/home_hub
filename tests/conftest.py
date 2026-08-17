import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

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
def session(engine):
    with Session(engine) as s:
        yield s

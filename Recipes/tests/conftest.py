import pytest
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Initialize the test database once per session."""
    from app.database import init_db
    init_db()
    yield


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

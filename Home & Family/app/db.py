"""Database engine and session management."""

from sqlmodel import Session, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def get_session():
    """Yield a database session. Used as a FastAPI dependency."""
    with Session(engine) as session:
        yield session

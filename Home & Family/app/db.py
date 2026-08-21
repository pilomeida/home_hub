"""Database engine and session management."""

from sqlalchemy import event
from sqlmodel import Session, create_engine

from app.config import settings


def register_foreign_keys_pragma(target_engine):
    """SQLite ignores FOREIGN KEY constraints unless a connection turns
    enforcement on explicitly."""

    @event.listens_for(target_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)
register_foreign_keys_pragma(engine)


def get_session():
    """Yield a database session. Used as a FastAPI dependency."""
    with Session(engine) as session:
        yield session

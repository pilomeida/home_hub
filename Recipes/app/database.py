"""Database engine and session management."""

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},  # SQLite single-thread workaround
)


def init_db():
    """Create all tables if they don't exist. Enable WAL mode."""
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def get_session():
    """Yield a database session. Used as FastAPI dependency."""
    with Session(engine) as session:
        yield session

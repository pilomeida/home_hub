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
    migrate_db()


def get_session():
    """Yield a database session. Used as FastAPI dependency."""
    with Session(engine) as session:
        yield session


_NEW_COLUMNS = [
    ("protein_g",     "INTEGER"),
    ("fat_g",         "INTEGER"),
    ("carbs_g",       "INTEGER"),
    ("fiber_g",       "INTEGER"),
    ("cooking_types", "TEXT DEFAULT '[]'"),
    ("source_title",  "VARCHAR"),
    ("notes",         "TEXT"),
]


def migrate_db(db_engine=None):
    """Add any missing columns to the recipes table (idempotent)."""
    from sqlalchemy import text
    target = db_engine or engine
    with target.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(recipes)"))}
        for col_name, col_type in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE recipes ADD COLUMN {col_name} {col_type}"))
        conn.commit()

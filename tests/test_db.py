from sqlmodel import create_engine

from app.db import register_foreign_keys_pragma


def test_foreign_keys_pragma_enabled_on_connect(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fk_test.db'}", connect_args={"check_same_thread": False}
    )
    register_foreign_keys_pragma(engine)

    with engine.connect() as conn:
        result = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()

    assert result == 1

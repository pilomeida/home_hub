"""The migrations form one line. Two migrations on the same parent leave two heads, and the deploy's
`alembic upgrade head` then refuses to run (2026-11-03: dental leaf and notices both on e3a9c5d1b742)."""

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_the_migrations_have_exactly_one_head():
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, f"two or more migrations share a parent; heads: {heads}. Re-point the newest one's down_revision."

"""Notices: what the Hub has sent to a person's phone, so each thing is told once (work-list C6)

The loan red flags already open are recorded as known: they have been on the Overview for weeks, and the
first run must not send fifteen of them at once. Everything else is sent when the job first finds it.

Revision ID: b4e7a2c9d013
Revises: e3a9c5d1b742
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "b4e7a2c9d013"
down_revision = "e3a9c5d1b742"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("reminded_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("kind", "ref", name="uq_notices_kind_ref"),
    )
    now = datetime.utcnow()
    bind = op.get_bind()
    open_flags = bind.execute(sa.text("select id, message from loan_alerts where acknowledged = 0")).all()
    notices = sa.table("notices", sa.column("kind"), sa.column("ref"), sa.column("text"), sa.column("link"),
                       sa.column("first_seen"), sa.column("sent_at"))
    if open_flags:
        op.bulk_insert(notices, [{"kind": "loan_flag", "ref": f"loan-alert:{i}", "text": "known before notices started",
                                  "link": "/financials/loans", "first_seen": now, "sent_at": now} for i, _ in open_flags])


def downgrade() -> None:
    op.drop_table("notices")

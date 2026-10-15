"""job_schedules, job_runs (schedules editable on the Hub) and problem_entries (what could not be done, and why)

Revision ID: c2f8b4d6e931
Revises: b9d5f3a7c124
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2f8b4d6e931"
down_revision: Union[str, Sequence[str], None] = "b9d5f3a7c124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_schedules",
        sa.Column("job", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("every_n", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("time_of_day", sa.String(), nullable=False, server_default="04:30"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job", sa.String(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False, server_default="running"),
        sa.Column("summary", sa.String(), nullable=True),
    )
    op.create_index("ix_job_runs_job", "job_runs", ["job"])
    op.create_table(
        "problem_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("area", sa.String(), nullable=False),
        sa.Column("what", sa.String(), nullable=False),
        sa.Column("why", sa.String(), nullable=False),
        sa.Column("next_step", sa.String(), nullable=False),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("technical", sa.String(), nullable=True),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("times", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
    )
    op.create_index("ix_problem_entries_at", "problem_entries", ["at"])
    op.create_index("ix_problem_entries_ref", "problem_entries", ["ref"])


def downgrade() -> None:
    op.drop_index("ix_problem_entries_ref", table_name="problem_entries")
    op.drop_index("ix_problem_entries_at", table_name="problem_entries")
    op.drop_table("problem_entries")
    op.drop_index("ix_job_runs_job", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("job_schedules")

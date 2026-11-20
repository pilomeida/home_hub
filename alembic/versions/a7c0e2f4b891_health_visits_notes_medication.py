"""Health visits, notes and medication, with medication changes kept (PLAN step 7c)

Revision ID: a7c0e2f4b891
Revises: f6b9d1e3a780
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c0e2f4b891"
down_revision = "f6b9d1e3a780"
branch_labels = None
depends_on = None


def _who():
    return [sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=False),
            sa.Column("condition_id", sa.Integer(), sa.ForeignKey("health_conditions.id"), nullable=True)]


def _by():
    return [sa.Column("created_by", sa.String(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False)]


def upgrade() -> None:
    op.create_table("health_visits", sa.Column("id", sa.Integer(), primary_key=True), *_who(),
                    sa.Column("status", sa.String(), nullable=False, server_default="booked"),
                    sa.Column("visit_on", sa.Date(), nullable=False), sa.Column("clinic", sa.String(), nullable=True),
                    sa.Column("doctor", sa.String(), nullable=True), sa.Column("observations", sa.String(), nullable=True),
                    sa.Column("plan_changes", sa.String(), nullable=True),
                    sa.Column("next_appointment_on", sa.Date(), nullable=True), *_by())
    op.create_table("health_notes", sa.Column("id", sa.Integer(), primary_key=True), *_who(),
                    sa.Column("kind", sa.String(), nullable=False, server_default="note"),
                    sa.Column("noted_on", sa.Date(), nullable=False), sa.Column("text", sa.String(), nullable=False),
                    sa.Column("value", sa.Float(), nullable=True), sa.Column("unit", sa.String(), nullable=True), *_by())
    op.create_table("health_medications", sa.Column("id", sa.Integer(), primary_key=True), *_who(),
                    sa.Column("name", sa.String(), nullable=False), sa.Column("dose", sa.String(), nullable=True),
                    sa.Column("frequency", sa.String(), nullable=True), sa.Column("started_on", sa.Date(), nullable=True),
                    sa.Column("stopped_on", sa.Date(), nullable=True), *_by())
    op.create_table("health_medication_changes", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("medication_id", sa.Integer(), sa.ForeignKey("health_medications.id"), nullable=False),
                    sa.Column("changed_on", sa.Date(), nullable=False), sa.Column("change", sa.String(), nullable=False),
                    sa.Column("dose", sa.String(), nullable=True), sa.Column("frequency", sa.String(), nullable=True),
                    sa.Column("visit_id", sa.Integer(), sa.ForeignKey("health_visits.id"), nullable=True), *_by())
    for table in ("health_visits", "health_notes", "health_medications"):
        op.create_index(f"ix_{table}_person_id", table, ["person_id"])
        op.create_index(f"ix_{table}_condition_id", table, ["condition_id"])
    op.create_index("ix_health_medication_changes_medication_id", "health_medication_changes", ["medication_id"])


def downgrade() -> None:
    op.drop_table("health_medication_changes")
    for table in ("health_medications", "health_notes", "health_visits"):
        op.drop_table(table)

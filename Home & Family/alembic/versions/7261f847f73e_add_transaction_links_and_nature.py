"""add transaction links and nature

Revision ID: 7261f847f73e
Revises: 6f861bafbb41
Create Date: 2026-08-21 16:15:18.777527

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '7261f847f73e'
down_revision: Union[str, Sequence[str], None] = '6f861bafbb41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('commitment_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('debt_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('nature', sa.Enum('ESSENTIAL', 'DISCRETIONARY', name='nature'), nullable=True))
        batch_op.create_foreign_key('fk_transactions_account_id', 'accounts', ['account_id'], ['id'])
        batch_op.create_foreign_key('fk_transactions_commitment_id', 'commitments', ['commitment_id'], ['id'])
        batch_op.create_foreign_key('fk_transactions_debt_id', 'debts', ['debt_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.drop_constraint('fk_transactions_debt_id', type_='foreignkey')
        batch_op.drop_constraint('fk_transactions_commitment_id', type_='foreignkey')
        batch_op.drop_constraint('fk_transactions_account_id', type_='foreignkey')
        batch_op.drop_column('nature')
        batch_op.drop_column('debt_id')
        batch_op.drop_column('commitment_id')
        batch_op.drop_column('account_id')

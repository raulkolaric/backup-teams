"""add identity columns to user

Revision ID: 3ef87dd3cf54
Revises: 80265b00351b
Create Date: 2026-02-28 23:07:33.246547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ef87dd3cf54'
down_revision: Union[str, Sequence[str], None] = '80265b00351b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('user', sa.Column('google_id', sa.Text(), nullable=True))
    op.add_column('user', sa.Column('avatar_url', sa.Text(), nullable=True))
    op.add_column('user', sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user', 'is_active')
    op.drop_column('user', 'avatar_url')
    op.drop_column('user', 'google_id')

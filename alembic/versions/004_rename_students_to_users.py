"""rename_students_to_users

Revision ID: 80265b00351b
Revises: 003
Create Date: 2026-02-28 18:13:37.054744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80265b00351b'
down_revision: Union[str, Sequence[str], None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Rename table
    op.rename_table('student', 'user')

    # 2. Add requested auth columns
    op.add_column('user', sa.Column('hashed_password', sa.Text(), nullable=True))
    op.add_column('user', sa.Column('msteams_email', sa.Text(), nullable=True))
    
    # Provide the standard OAuth structure (Refresh Token) instead of raw password where possible
    op.add_column('user', sa.Column('msteams_refresh_token', sa.Text(), nullable=True))

    # Optional: If you STILL intend to capture/use their raw MS teams password and just encrypt it locally
    op.add_column('user', sa.Column('msteams_password_encrypted', sa.Text(), nullable=True))
    
    # 3. Handle Postgres sequence / constraint renaming safely (if index exists)
    op.execute('ALTER INDEX IF EXISTS idx_student_email RENAME TO idx_user_email')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('ALTER INDEX IF EXISTS idx_user_email RENAME TO idx_student_email')

    op.drop_column('user', 'msteams_password_encrypted')
    op.drop_column('user', 'msteams_refresh_token')
    op.drop_column('user', 'msteams_email')
    op.drop_column('user', 'hashed_password')

    op.rename_table('user', 'student')

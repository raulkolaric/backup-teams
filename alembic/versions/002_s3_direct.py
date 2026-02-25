"""
alembic/versions/002_s3_direct.py

Remove local_path NOT NULL constraint and rename semantics:
  - local_path becomes nullable — it is NULL when using S3-direct mode
  - This migration allows the scraper to run without writing anything to disk

To apply: alembic upgrade head
To revert: alembic downgrade -1
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make local_path nullable — S3-direct mode has no local path
    op.alter_column(
        "archive",
        "local_path",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Revert: local_path required again
    # Note: any NULL rows will cause this to fail unless backfilled first
    op.alter_column(
        "archive",
        "local_path",
        existing_type=sa.Text(),
        nullable=False,
    )

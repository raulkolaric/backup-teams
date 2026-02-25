"""
alembic/versions/003_content_fts.py

Add full-text search to the archive table:
  - content_text TEXT       : raw PDF text extracted by src/indexer.py
  - content_tsv tsvector    : auto-updated via trigger (portuguese stemming)
  - GIN index on content_tsv: makes @@ queries fast
"""
from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Raw text column — populated by the indexer
    op.add_column("archive", sa.Column("content_text", sa.Text, nullable=True))

    # tsvector column — must be added via raw DDL (not a standard SQLAlchemy type)
    op.execute("ALTER TABLE archive ADD COLUMN content_tsv tsvector")

    # GIN index for fast full-text @@ queries
    op.execute(
        "CREATE INDEX ix_archive_content_tsv ON archive USING GIN(content_tsv)"
    )

    # Trigger function: auto-rebuild tsvector on content_text change
    op.execute("""
        CREATE OR REPLACE FUNCTION archive_tsv_trigger() RETURNS trigger AS $$
        BEGIN
            NEW.content_tsv :=
                to_tsvector('portuguese', coalesce(NEW.content_text, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER tsvector_update
            BEFORE INSERT OR UPDATE OF content_text
            ON archive
            FOR EACH ROW EXECUTE FUNCTION archive_tsv_trigger();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tsvector_update ON archive")
    op.execute("DROP FUNCTION IF EXISTS archive_tsv_trigger")
    op.execute("DROP INDEX IF EXISTS ix_archive_content_tsv")
    op.drop_column("archive", "content_tsv")
    op.drop_column("archive", "content_text")

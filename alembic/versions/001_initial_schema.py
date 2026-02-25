"""
001_initial_schema.py — Initial database schema.

Creates all tables from scratch:
  - professor
  - curso
  - class
  - student
  - archive (includes s3_key + contributed_by for future multi-student support)

Downgrade drops all tables in reverse FK order.
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None       # first migration — no parent
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── pgcrypto for gen_random_uuid() ────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── professor ─────────────────────────────────────────────────────────────
    op.create_table(
        "professor",
        sa.Column("id",         sa.UUID(),          primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",       sa.Text(),           nullable=False),
        sa.Column("email",      sa.Text(),           nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── curso ─────────────────────────────────────────────────────────────────
    op.create_table(
        "curso",
        sa.Column("id",         sa.UUID(),          primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",       sa.Text(),           nullable=False),
        sa.Column("teams_id",   sa.Text(),           unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── class ─────────────────────────────────────────────────────────────────
    op.create_table(
        "class",
        sa.Column("id",               sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",             sa.Text(), nullable=False),
        sa.Column("curso_id",         sa.UUID(), sa.ForeignKey("curso.id",     ondelete="CASCADE")),
        sa.Column("professor_id",     sa.UUID(), sa.ForeignKey("professor.id", ondelete="SET NULL"), nullable=True),
        sa.Column("semester",         sa.Text(), nullable=False),
        sa.Column("class_year",       sa.Integer(), nullable=False),
        sa.Column("teams_channel_id", sa.Text(), unique=True),
        sa.Column("created_at",       sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── student ───────────────────────────────────────────────────────────────
    op.create_table(
        "student",
        sa.Column("id",         sa.UUID(),          primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name",       sa.Text(),           nullable=False),
        sa.Column("email",      sa.Text(),           nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── archive ───────────────────────────────────────────────────────────────
    op.create_table(
        "archive",
        sa.Column("id",              sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id",        sa.UUID(), sa.ForeignKey("class.id",   ondelete="CASCADE"),   nullable=False),
        sa.Column("contributed_by",  sa.UUID(), sa.ForeignKey("student.id", ondelete="SET NULL"),  nullable=True),
        sa.Column("file_name",       sa.Text(), nullable=False),
        sa.Column("file_extension",  sa.Text(), nullable=False),
        sa.Column("local_path",      sa.Text(), nullable=False),
        sa.Column("s3_key",          sa.Text(), nullable=True),
        sa.Column("drive_item_id",   sa.Text(), nullable=False, unique=True),
        sa.Column("etag",            sa.Text(), nullable=True),
        sa.Column("created_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at",      sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index("idx_archive_drive_item",  "archive", ["drive_item_id"])
    op.create_index("idx_archive_class",       "archive", ["class_id"])
    op.create_index("idx_archive_contributed", "archive", ["contributed_by"])
    op.create_index("idx_student_email",       "student", ["email"])


def downgrade() -> None:
    op.drop_table("archive")
    op.drop_table("student")
    op.drop_table("class")
    op.drop_table("curso")
    op.drop_table("professor")

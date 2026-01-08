"""add document hash and analysis cache

Revision ID: 2b1e9b0b8d6a
Revises: 9f8974f71158
Create Date: 2026-01-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2b1e9b0b8d6a"
down_revision = "9f8974f71158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("document_hash", sa.String(), nullable=True))
    op.create_index("ix_reports_document_hash", "reports", ["document_hash"])

    op.create_table(
        "document_analysis_cache",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("document_hash", sa.String(), nullable=False),
        sa.Column("scoring_model_sha", sa.String(), nullable=True),
        sa.Column("pipeline_git_sha", sa.String(), nullable=True),
        sa.Column("detected_points", sa.JSON(), nullable=True),
        sa.Column("scoring_result", sa.JSON(), nullable=True),
        sa.Column("ai_analysis", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_document_analysis_cache_hash",
        "document_analysis_cache",
        ["document_hash", "scoring_model_sha", "pipeline_git_sha"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_document_analysis_cache_hash", table_name="document_analysis_cache")
    op.drop_table("document_analysis_cache")
    op.drop_index("ix_reports_document_hash", table_name="reports")
    op.drop_column("reports", "document_hash")

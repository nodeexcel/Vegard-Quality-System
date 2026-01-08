"""Add detected_points and scoring_result to reports

Revision ID: 0f3d9f7c6b7a
Revises: db595064c79e
Create Date: 2025-12-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0f3d9f7c6b7a'
down_revision = 'db595064c79e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reports', sa.Column('detected_points', sa.JSON(), nullable=True))
    op.add_column('reports', sa.Column('scoring_result', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('reports', 'scoring_result')
    op.drop_column('reports', 'detected_points')

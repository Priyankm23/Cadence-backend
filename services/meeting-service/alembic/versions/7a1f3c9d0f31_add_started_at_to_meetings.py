"""add started_at to meetings

Revision ID: 7a1f3c9d0f31
Revises: 43d29b761bcb
Create Date: 2026-05-18 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1f3c9d0f31'
down_revision: Union[str, Sequence[str], None] = '43d29b761bcb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('meetings', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meetings', 'started_at')

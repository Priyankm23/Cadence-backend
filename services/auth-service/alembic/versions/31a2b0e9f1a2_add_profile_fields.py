"""add profile fields

Revision ID: 31a2b0e9f1a2
Revises: eccf82689154
Create Date: 2026-06-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31a2b0e9f1a2'
down_revision: Union[str, Sequence[str], None] = 'eccf82689154'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('company_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('role', sa.String(), nullable=True))
    op.add_column('users', sa.Column('photo_url', sa.String(), nullable=True))
    op.add_column('users', sa.Column('meeting_name', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'meeting_name')
    op.drop_column('users', 'photo_url')
    op.drop_column('users', 'role')
    op.drop_column('users', 'company_name')

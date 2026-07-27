"""add profile fields and report delivery

Revision ID: 41b2c3d9e4a3
Revises: 0c4db66bb55a
Create Date: 2026-06-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41b2c3d9e4a3'
down_revision: Union[str, Sequence[str], None] = '0c4db66bb55a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('company_name', sa.String(), nullable=True))
    op.add_column('users', sa.Column('role', sa.String(), nullable=True))
    op.add_column('users', sa.Column('photo_url', sa.String(), nullable=True))
    op.add_column('users', sa.Column('meeting_name', sa.String(), nullable=True))
    
    op.add_column('meeting_participants', sa.Column('receive_report', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('meeting_participants', sa.Column('share_company_details', sa.Boolean(), server_default='true', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meeting_participants', 'share_company_details')
    op.drop_column('meeting_participants', 'receive_report')
    
    op.drop_column('users', 'meeting_name')
    op.drop_column('users', 'photo_url')
    op.drop_column('users', 'role')
    op.drop_column('users', 'company_name')


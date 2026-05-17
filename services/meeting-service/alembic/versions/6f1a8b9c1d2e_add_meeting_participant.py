"""add meeting_participant

Revision ID: 6f1a8b9c1d2e
Revises: 5e6900c2d109
Create Date: 2026-05-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f1a8b9c1d2e'
down_revision: Union[str, Sequence[str], None] = '5e6900c2d109'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('meeting_participants',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('meeting_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('speaking_time_seconds', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('meeting_participants')

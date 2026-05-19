"""merge heads

Revision ID: a343c508c2d9
Revises: 6bc476c36ffd, 7a1f3c9d0f31
Create Date: 2026-05-18 13:55:40.981344

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a343c508c2d9'
down_revision: Union[str, Sequence[str], None] = ('6bc476c36ffd', '7a1f3c9d0f31')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

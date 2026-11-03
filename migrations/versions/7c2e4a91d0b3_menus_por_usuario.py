"""menus por usuario

Revision ID: 7c2e4a91d0b3
Revises: 1e65a6b27a61
Create Date: 2026-11-03 16:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7c2e4a91d0b3'
down_revision: Union[str, None] = '1e65a6b27a61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('menus_bloqueados', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('menus_bloqueados')

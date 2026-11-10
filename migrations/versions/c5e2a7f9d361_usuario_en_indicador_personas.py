"""usuario visible en el indicador de cada persona

Revision ID: c5e2a7f9d361
Revises: b3f6c8d2e417
Create Date: 2026-11-09 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e2a7f9d361'
down_revision: Union[str, None] = 'b3f6c8d2e417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('en_indicador_personas', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('en_indicador_personas')

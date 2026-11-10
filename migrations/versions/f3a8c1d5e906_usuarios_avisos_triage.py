"""usuarios: casilla para recibir o no el aviso de cada ticket nuevo por clasificar

Revision ID: f3a8c1d5e906
Revises: e1f5b9c3a742
Create Date: 2026-11-10 17:50:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a8c1d5e906'
down_revision: Union[str, None] = 'e1f5b9c3a742'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('avisos_triage', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('avisos_triage')

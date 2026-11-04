"""participantes de sesion como etiquetas

Revision ID: b4e8a1c2d7f9
Revises: 9d1f3b7c5e20
Create Date: 2026-11-03 19:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8a1c2d7f9'
down_revision: Union[str, None] = '9d1f3b7c5e20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sesiones_cliente') as b:
        b.add_column(sa.Column('participantes_det', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('sesiones_cliente') as b:
        b.drop_column('participantes_det')

"""kardex de lo que hace OneCore confirmado desde el benchmark

Revision ID: e8b4f2c6a713
Revises: d5a7c3e9b142
Create Date: 2026-11-26 12:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8b4f2c6a713'
down_revision: Union[str, None] = 'd5a7c3e9b142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('modulos_onecore') as b:
        b.add_column(sa.Column('confirmaciones', sa.JSON(), nullable=True))
    with op.batch_alter_table('insights_benchmark') as b:
        b.add_column(sa.Column('onecore', sa.String(8), nullable=False, server_default=''))
        b.add_column(sa.Column('onecore_modulo_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_insight_modulo', 'modulos_onecore', ['onecore_modulo_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('insights_benchmark') as b:
        b.drop_constraint('fk_insight_modulo', type_='foreignkey')
        b.drop_column('onecore_modulo_id')
        b.drop_column('onecore')
    with op.batch_alter_table('modulos_onecore') as b:
        b.drop_column('confirmaciones')

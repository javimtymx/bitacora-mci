"""hallazgos de voz del cliente: trabajo del cliente (JTBD) y oportunidad accionable para OneCore

Revision ID: d3f9b6a1c457
Revises: c5a1e7d3f218
Create Date: 2026-11-11 15:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f9b6a1c457'
down_revision: Union[str, None] = 'c5a1e7d3f218'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('hallazgos') as b:
        b.add_column(sa.Column('trabajo', sa.Text(), nullable=True))
        b.add_column(sa.Column('oportunidad', sa.Text(), nullable=True))
        b.add_column(sa.Column('tipo_oportunidad', sa.String(15), nullable=True))
        b.add_column(sa.Column('idea_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_hallazgo_idea', 'ideas', ['idea_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('hallazgos') as b:
        b.drop_constraint('fk_hallazgo_idea', type_='foreignkey')
        for c in ('idea_id', 'tipo_oportunidad', 'oportunidad', 'trabajo'):
            b.drop_column(c)

"""compromisos: subtareas (vista de tablero por área)

Revision ID: a4c7e2d9b513
Revises: f3a8c1d5e906
Create Date: 2026-11-10 21:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4c7e2d9b513'
down_revision: Union[str, None] = 'f3a8c1d5e906'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'compromiso_subtareas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('compromiso_id', sa.Integer(), sa.ForeignKey('compromisos.id', name='fk_subt_comp'), nullable=False),
        sa.Column('texto', sa.String(500), nullable=False),
        sa.Column('responsable_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_subt_resp'), nullable=True),
        sa.Column('fecha', sa.Date(), nullable=True),
        sa.Column('hecha', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('hecha_en', sa.DateTime(), nullable=True),
        sa.Column('orden', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_subt_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_compromiso_subtareas_compromiso_id', 'compromiso_subtareas', ['compromiso_id'])


def downgrade() -> None:
    op.drop_index('ix_compromiso_subtareas_compromiso_id', table_name='compromiso_subtareas')
    op.drop_table('compromiso_subtareas')

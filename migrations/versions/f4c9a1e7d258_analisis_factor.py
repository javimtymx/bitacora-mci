"""explicación por factor del lienzo estratégico (qué hace mejor el competidor y cómo competir)

Revision ID: f4c9a1e7d258
Revises: e8b4f2c6a713
Create Date: 2026-11-26 16:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4c9a1e7d258'
down_revision: Union[str, None] = 'e8b4f2c6a713'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'analisis_factor',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('clave', sa.String(160), nullable=False),
        sa.Column('factor', sa.String(160), nullable=False),
        sa.Column('firma', sa.String(64), nullable=False, server_default=''),
        sa.Column('resultado', sa.JSON(), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_anfactor_usuario'), nullable=True),
    )
    op.create_index('ix_analisis_factor_clave', 'analisis_factor', ['clave'])


def downgrade() -> None:
    op.drop_index('ix_analisis_factor_clave', table_name='analisis_factor')
    op.drop_table('analisis_factor')

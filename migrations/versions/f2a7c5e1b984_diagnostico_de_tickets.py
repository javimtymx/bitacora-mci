"""diagnostico de tickets por area o producto

Revision ID: f2a7c5e1b984
Revises: e8c4a2d9f173
Create Date: 2026-11-04 15:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a7c5e1b984'
down_revision: Union[str, None] = 'e8c4a2d9f173'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ticket_diagnosticos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('dimension', sa.String(length=12), nullable=False),
        sa.Column('dias', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=12), nullable=False),
        sa.Column('resultado', sa.JSON(), nullable=True),
        sa.Column('modelo', sa.String(length=60), nullable=False),
        sa.Column('generado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tdiag_usuario'), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_diagnosticos_creado', 'ticket_diagnosticos', ['creado'])


def downgrade() -> None:
    op.drop_index('ix_ticket_diagnosticos_creado', 'ticket_diagnosticos')
    op.drop_table('ticket_diagnosticos')

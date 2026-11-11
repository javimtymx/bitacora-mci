"""clientes: citas recurrentes, módulo y delegación a soporte de pendientes, análisis de recurrencias de servicio

Revision ID: e6c2a8d4b791
Revises: d3f9b6a1c457
Create Date: 2026-11-11 17:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6c2a8d4b791'
down_revision: Union[str, None] = 'd3f9b6a1c457'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('agenda_clientes') as b:
        b.add_column(sa.Column('serie_id', sa.String(36), nullable=True))
        b.add_column(sa.Column('recurrencia', sa.String(10), nullable=True))
        b.create_index('ix_agenda_clientes_serie_id', ['serie_id'])
    with op.batch_alter_table('pendientes_cliente') as b:
        b.add_column(sa.Column('producto', sa.String(80), nullable=False, server_default=''))
        b.add_column(sa.Column('menu', sa.String(120), nullable=False, server_default=''))
        b.add_column(sa.Column('ticket_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_pend_ticket', 'tickets', ['ticket_id'], ['id'])
        b.create_index('ix_pendientes_cliente_ticket_id', ['ticket_id'])
    op.create_table(
        'analisis_servicio',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('desde', sa.Date(), nullable=False),
        sa.Column('hasta', sa.Date(), nullable=False),
        sa.Column('estado', sa.String(12), nullable=False, server_default='analizando'),
        sa.Column('resultado', sa.JSON(), nullable=True),
        sa.Column('autor_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_anserv_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('analisis_servicio')
    with op.batch_alter_table('pendientes_cliente') as b:
        b.drop_index('ix_pendientes_cliente_ticket_id')
        b.drop_constraint('fk_pend_ticket', type_='foreignkey')
        for c in ('ticket_id', 'menu', 'producto'):
            b.drop_column(c)
    with op.batch_alter_table('agenda_clientes') as b:
        b.drop_index('ix_agenda_clientes_serie_id')
        b.drop_column('recurrencia')
        b.drop_column('serie_id')

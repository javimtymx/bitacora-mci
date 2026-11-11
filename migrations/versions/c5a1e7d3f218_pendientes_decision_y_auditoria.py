"""pendientes de clientes: decisión del operativo (pasa o no al kanban) y auditoría de Customer Success

Revision ID: c5a1e7d3f218
Revises: b8e3d1f6a924
Create Date: 2026-11-11 14:50:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5a1e7d3f218'
down_revision: Union[str, None] = 'b8e3d1f6a924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('pendientes_cliente') as b:
        b.add_column(sa.Column('decision_motivo', sa.Text(), nullable=True))
        b.add_column(sa.Column('decidido_por', sa.Integer(), nullable=True))
        b.add_column(sa.Column('decidido_en', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('auditoria_motivo', sa.Text(), nullable=True))
        b.add_column(sa.Column('auditado_por', sa.Integer(), nullable=True))
        b.add_column(sa.Column('auditado_en', sa.DateTime(), nullable=True))
        b.create_foreign_key('fk_pend_decisor', 'usuarios', ['decidido_por'], ['id'])
        b.create_foreign_key('fk_pend_auditor', 'usuarios', ['auditado_por'], ['id'])
    # el estado «descartado» de la versión anterior pasa a «histórico» (lo audita Customer Success)
    op.execute("UPDATE pendientes_cliente SET estado = 'historico' WHERE estado = 'descartado'")


def downgrade() -> None:
    op.execute("UPDATE pendientes_cliente SET estado = 'descartado' WHERE estado IN ('historico', 'no_vigente')")
    with op.batch_alter_table('pendientes_cliente') as b:
        b.drop_constraint('fk_pend_auditor', type_='foreignkey')
        b.drop_constraint('fk_pend_decisor', type_='foreignkey')
        for c in ('auditado_en', 'auditado_por', 'auditoria_motivo', 'decidido_en', 'decidido_por', 'decision_motivo'):
            b.drop_column(c)

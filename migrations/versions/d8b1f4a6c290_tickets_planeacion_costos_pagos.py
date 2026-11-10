"""tickets: jefe directo y costo por hora de usuarios, planeación, horas, costos, pagos, copias y archivos grandes

Revision ID: d8b1f4a6c290
Revises: c5e2a7f9d361
Create Date: 2026-11-10 15:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8b1f4a6c290'
down_revision: Union[str, None] = 'c5e2a7f9d361'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('jefe_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_usuario_jefe'), nullable=True))
        b.add_column(sa.Column('costo_hora', sa.Numeric(10, 2), nullable=True))
    op.create_table(
        'ticket_pagos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ingeniero_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tpago_ingeniero'), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('monto', sa.Numeric(12, 2), nullable=False),
        sa.Column('nota', sa.Text(), nullable=True),
        sa.Column('comprobante_ruta', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('comprobante_nombre', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('comprobante_tipo', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('comprobante_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('registrado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tpago_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_pagos_ingeniero_id', 'ticket_pagos', ['ingeniero_id'])
    with op.batch_alter_table('tickets') as b:
        b.add_column(sa.Column('cc', sa.JSON(), nullable=True))
        b.add_column(sa.Column('horas_estimadas', sa.Numeric(8, 2), nullable=True))
        b.add_column(sa.Column('fecha_entrega', sa.Date(), nullable=True))
        b.add_column(sa.Column('planeado_en', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('horas_reales', sa.Numeric(8, 2), nullable=True))
        b.add_column(sa.Column('costo_hora', sa.Numeric(10, 2), nullable=True))
        b.add_column(sa.Column('pago_id', sa.Integer(), sa.ForeignKey('ticket_pagos.id', name='fk_ticket_pago'), nullable=True))
        b.create_index('ix_tickets_pago_id', ['pago_id'])
    # archivos de más de 2 GB
    with op.batch_alter_table('ticket_anexos') as b:
        b.alter_column('bytes', existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)
    with op.batch_alter_table('evidencias_archivo') as b:
        b.alter_column('bytes', existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('evidencias_archivo') as b:
        b.alter_column('bytes', existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
    with op.batch_alter_table('ticket_anexos') as b:
        b.alter_column('bytes', existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
    with op.batch_alter_table('tickets') as b:
        b.drop_index('ix_tickets_pago_id')
        for c in ('pago_id', 'costo_hora', 'horas_reales', 'planeado_en', 'fecha_entrega', 'horas_estimadas', 'cc'):
            b.drop_column(c)
    op.drop_index('ix_ticket_pagos_ingeniero_id', 'ticket_pagos')
    op.drop_table('ticket_pagos')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('costo_hora')
        b.drop_column('jefe_id')

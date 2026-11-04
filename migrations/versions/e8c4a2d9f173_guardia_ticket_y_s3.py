"""GuardIA Ticket y transcripciones en S3

Revision ID: e8c4a2d9f173
Revises: d7b3f0a6c915
Create Date: 2026-11-04 15:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8c4a2d9f173'
down_revision: Union[str, None] = 'd7b3f0a6c915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sesiones_cliente') as b:
        b.add_column(sa.Column('transcripcion_ruta', sa.String(length=300), nullable=True))
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('menus_extra', sa.JSON(), nullable=True))

    op.create_table(
        'ticket_catalogo',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('producto', sa.String(length=80), nullable=False),
        sa.Column('menu', sa.String(length=120), nullable=False),
        sa.Column('pantalla', sa.String(length=160), nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint('producto', 'menu', 'pantalla', name='uq_ticket_catalogo'),
    )
    op.create_table(
        'ticket_prompts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False, unique=True),
        sa.Column('instrucciones', sa.Text(), nullable=False),
        sa.Column('reglas', sa.JSON(), nullable=True),
        sa.Column('requeridos', sa.JSON(), nullable=True),
        sa.Column('motivo', sa.Text(), nullable=True),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tprompt_usuario'), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'tickets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('impacto', sa.String(length=20), nullable=False),
        sa.Column('producto', sa.String(length=80), nullable=False),
        sa.Column('menu', sa.String(length=120), nullable=False),
        sa.Column('pantalla', sa.String(length=160), nullable=False),
        sa.Column('cliente', sa.String(length=200), nullable=False),
        sa.Column('referencia', sa.String(length=300), nullable=False),
        sa.Column('pasos', sa.Text(), nullable=False),
        sa.Column('esperado', sa.Text(), nullable=False),
        sa.Column('obtenido', sa.Text(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('estado_previo', sa.String(length=20), nullable=True),
        sa.Column('prioridad', sa.String(length=2), nullable=True),
        sa.Column('asignado_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_ticket_asignado'), nullable=True),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_ticket_autor'), nullable=False),
        sa.Column('validacion', sa.String(length=20), nullable=False),
        sa.Column('faltantes', sa.JSON(), nullable=True),
        sa.Column('captura', sa.JSON(), nullable=True),
        sa.Column('prompt_version', sa.Integer(), nullable=True),
        sa.Column('ia_triage', sa.JSON(), nullable=True),
        sa.Column('sla_vence', sa.DateTime(), nullable=True),
        sa.Column('paquete_cursor', sa.Text(), nullable=True),
        sa.Column('resolucion', sa.JSON(), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('actualizado', sa.DateTime(), nullable=False),
        sa.Column('triage_en', sa.DateTime(), nullable=True),
        sa.Column('cerrado_en', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_tickets_estado', 'tickets', ['estado'])
    op.create_index('ix_tickets_asignado_id', 'tickets', ['asignado_id'])
    op.create_index('ix_tickets_creado_por', 'tickets', ['creado_por'])
    op.create_index('ix_tickets_creado', 'tickets', ['creado'])
    op.create_table(
        'ticket_anexos',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id', name='fk_tanexo_ticket'), nullable=False),
        sa.Column('nombre', sa.String(length=255), nullable=False),
        sa.Column('ruta', sa.String(length=300), nullable=False),
        sa.Column('tipo', sa.String(length=120), nullable=False),
        sa.Column('bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('subido_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tanexo_usuario'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_anexos_ticket_id', 'ticket_anexos', ['ticket_id'])
    op.create_table(
        'ticket_comentarios',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id', name='fk_tcom_ticket'), nullable=False),
        sa.Column('autor_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tcom_usuario'), nullable=True),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_comentarios_ticket_id', 'ticket_comentarios', ['ticket_id'])
    op.create_table(
        'ticket_reglas',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id', name='fk_tregla_ticket'), nullable=True),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('propuesto_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tregla_autor'), nullable=False),
        sa.Column('estado', sa.String(length=12), nullable=False),
        sa.Column('resuelto_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_tregla_resolutor'), nullable=True),
        sa.Column('resuelto_en', sa.DateTime(), nullable=True),
        sa.Column('prompt_version', sa.Integer(), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_reglas_estado', 'ticket_reglas', ['estado'])


def downgrade() -> None:
    op.drop_index('ix_ticket_reglas_estado', 'ticket_reglas')
    op.drop_table('ticket_reglas')
    op.drop_index('ix_ticket_comentarios_ticket_id', 'ticket_comentarios')
    op.drop_table('ticket_comentarios')
    op.drop_index('ix_ticket_anexos_ticket_id', 'ticket_anexos')
    op.drop_table('ticket_anexos')
    for ix in ('ix_tickets_creado', 'ix_tickets_creado_por', 'ix_tickets_asignado_id', 'ix_tickets_estado'):
        op.drop_index(ix, 'tickets')
    op.drop_table('tickets')
    op.drop_table('ticket_prompts')
    op.drop_table('ticket_catalogo')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('menus_extra')
    with op.batch_alter_table('sesiones_cliente') as b:
        b.drop_column('transcripcion_ruta')

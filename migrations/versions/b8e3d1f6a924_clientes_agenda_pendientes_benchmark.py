"""clientes: agenda mensual (con avisos internos y puestos), pendientes (quejas/tareas), benchmark de competidores; evidencias retiradas

Revision ID: b8e3d1f6a924
Revises: a4c7e2d9b513
Create Date: 2026-11-11 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e3d1f6a924'
down_revision: Union[str, None] = 'a4c7e2d9b513'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEXTO_LARGO = sa.Text().with_variant(sa.Text(length=4294967295), "mysql")


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('puesto', sa.String(30), nullable=True))
        b.add_column(sa.Column('avisos_agenda', sa.Boolean(), nullable=True))
    with op.batch_alter_table('evidencias_archivo') as b:
        b.add_column(sa.Column('retirada', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('retirada_en', sa.DateTime(), nullable=True))
        b.add_column(sa.Column('retirada_por', sa.Integer(), nullable=True))
        b.add_column(sa.Column('retirada_motivo', sa.Text(), nullable=True))
        b.create_foreign_key('fk_evid_retiro', 'usuarios', ['retirada_por'], ['id'])
        b.create_index('ix_evidencias_archivo_retirada', ['retirada'])

    op.create_table(
        'agenda_clientes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('mes', sa.String(7), nullable=False),
        sa.Column('tipo', sa.String(12), nullable=False),
        sa.Column('area_id', sa.Integer(), sa.ForeignKey('areas.id', name='fk_agcli_area'), nullable=False),
        sa.Column('responsable_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_agcli_resp'), nullable=False),
        sa.Column('cliente_rfc', sa.String(13), nullable=False, server_default=''),
        sa.Column('cliente_nombre', sa.String(255), nullable=False, server_default=''),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('hora', sa.String(5), nullable=False, server_default=''),
        sa.Column('puntos', sa.JSON(), nullable=True),
        sa.Column('invitados', sa.JSON(), nullable=True),
        sa.Column('estado', sa.String(16), nullable=False, server_default='programada'),
        sa.Column('sesion_id', sa.Integer(), sa.ForeignKey('sesiones_cliente.id', name='fk_agcli_sesion'), nullable=True),
        sa.Column('ejecucion', sa.JSON(), nullable=True),
        sa.Column('historial', sa.JSON(), nullable=True),
        sa.Column('avisos', sa.JSON(), nullable=True),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_agcli_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    for c in ('mes', 'responsable_id', 'fecha', 'estado'):
        op.create_index(f'ix_agenda_clientes_{c}', 'agenda_clientes', [c])

    op.create_table(
        'pendientes_cliente',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tipo', sa.String(10), nullable=False, server_default='tarea'),
        sa.Column('titulo', sa.String(200), nullable=False),
        sa.Column('detalle', sa.Text(), nullable=True),
        sa.Column('cita', sa.Text(), nullable=True),
        sa.Column('severidad', sa.String(8), nullable=False, server_default='media'),
        sa.Column('cliente_rfc', sa.String(13), nullable=False, server_default=''),
        sa.Column('cliente_nombre', sa.String(255), nullable=False, server_default=''),
        sa.Column('origen', sa.String(10), nullable=False, server_default='manual'),
        sa.Column('agenda_id', sa.Integer(), sa.ForeignKey('agenda_clientes.id', name='fk_pend_agenda'), nullable=True),
        sa.Column('sesion_id', sa.Integer(), sa.ForeignKey('sesiones_cliente.id', name='fk_pend_sesion'), nullable=True),
        sa.Column('estado', sa.String(12), nullable=False, server_default='pendiente'),
        sa.Column('asignado_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_pend_asig'), nullable=True),
        sa.Column('fecha_compromiso', sa.Date(), nullable=True),
        sa.Column('resolucion', sa.Text(), nullable=True),
        sa.Column('resuelto_en', sa.DateTime(), nullable=True),
        sa.Column('veredicto', sa.String(20), nullable=True),
        sa.Column('verificacion', sa.JSON(), nullable=True),
        sa.Column('historial', sa.JSON(), nullable=True),
        sa.Column('creado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_pend_autor'), nullable=True),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    for c in ('agenda_id', 'sesion_id', 'estado', 'asignado_id'):
        op.create_index(f'ix_pendientes_cliente_{c}', 'pendientes_cliente', [c])

    op.create_table(
        'archivos_cliente',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entidad', sa.String(12), nullable=False),
        sa.Column('entidad_id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(255), nullable=False),
        sa.Column('ruta', sa.String(300), nullable=False),
        sa.Column('tipo', sa.String(120), nullable=False, server_default=''),
        sa.Column('bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('sha256', sa.String(64), nullable=False, server_default=''),
        sa.Column('subido_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_arcli_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('retirada', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('retirada_en', sa.DateTime(), nullable=True),
        sa.Column('retirada_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_arcli_retiro'), nullable=True),
        sa.Column('retirada_motivo', sa.Text(), nullable=True),
    )
    op.create_index('ix_archivos_cliente_entidad', 'archivos_cliente', ['entidad'])
    op.create_index('ix_archivos_cliente_entidad_id', 'archivos_cliente', ['entidad_id'])

    op.create_table(
        'competidores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('nombre', sa.String(160), nullable=False, unique=True),
        sa.Column('sitio', sa.String(255), nullable=False, server_default=''),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'sesiones_competidor',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('competidor_id', sa.Integer(), sa.ForeignKey('competidores.id', name='fk_sescomp_comp'), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('titulo', sa.String(200), nullable=False, server_default=''),
        sa.Column('contexto', sa.Text(), nullable=True),
        sa.Column('participantes', sa.String(300), nullable=False, server_default=''),
        sa.Column('transcripcion', TEXTO_LARGO, nullable=True),
        sa.Column('cifrada', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('transcripcion_ruta', sa.String(300), nullable=True),
        sa.Column('palabras', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estado', sa.String(12), nullable=False, server_default='cargada'),
        sa.Column('resultado', sa.JSON(), nullable=True),
        sa.Column('autor_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_sescomp_autor'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_sesiones_competidor_competidor_id', 'sesiones_competidor', ['competidor_id'])
    op.create_table(
        'insights_benchmark',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('sesion_id', sa.Integer(), sa.ForeignKey('sesiones_competidor.id', name='fk_insb_sesion'), nullable=False),
        sa.Column('tipo', sa.String(12), nullable=False),
        sa.Column('titulo', sa.String(200), nullable=False),
        sa.Column('detalle', sa.Text(), nullable=True),
        sa.Column('cita', sa.Text(), nullable=True),
        sa.Column('impacto', sa.String(8), nullable=False, server_default='media'),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_insights_benchmark_sesion_id', 'insights_benchmark', ['sesion_id'])
    op.create_index('ix_insights_benchmark_tipo', 'insights_benchmark', ['tipo'])


def downgrade() -> None:
    op.drop_table('insights_benchmark')
    op.drop_table('sesiones_competidor')
    op.drop_table('competidores')
    op.drop_table('archivos_cliente')
    op.drop_table('pendientes_cliente')
    op.drop_table('agenda_clientes')
    with op.batch_alter_table('evidencias_archivo') as b:
        b.drop_index('ix_evidencias_archivo_retirada')
        b.drop_constraint('fk_evid_retiro', type_='foreignkey')
        b.drop_column('retirada_motivo')
        b.drop_column('retirada_por')
        b.drop_column('retirada_en')
        b.drop_column('retirada')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('avisos_agenda')
        b.drop_column('puesto')

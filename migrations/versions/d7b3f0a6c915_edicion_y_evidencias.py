"""historial de ediciones, archivos de evidencia y verificacion de cierre

Revision ID: d7b3f0a6c915
Revises: c6a2e9f41b8d
Create Date: 2026-11-04 13:50:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7b3f0a6c915'
down_revision: Union[str, None] = 'c6a2e9f41b8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ediciones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entidad', sa.String(length=20), nullable=False),
        sa.Column('entidad_id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_ediciones_usuario'), nullable=False),
        sa.Column('antes', sa.JSON(), nullable=False),
        sa.Column('despues', sa.JSON(), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ediciones_entidad', 'ediciones', ['entidad'])
    op.create_index('ix_ediciones_entidad_id', 'ediciones', ['entidad_id'])
    op.create_table(
        'evidencias_archivo',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('compromiso_id', sa.Integer(), sa.ForeignKey('compromisos.id', name='fk_evid_compromiso'), nullable=False),
        sa.Column('nombre', sa.String(length=255), nullable=False),
        sa.Column('ruta', sa.String(length=300), nullable=False),
        sa.Column('tipo', sa.String(length=120), nullable=False),
        sa.Column('bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('subido_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_evid_usuario'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_evidencias_archivo_compromiso_id', 'evidencias_archivo', ['compromiso_id'])
    with op.batch_alter_table('compromisos') as b:
        b.add_column(sa.Column('cierre_veredicto', sa.String(length=20), nullable=True))
        b.add_column(sa.Column('cierre_detalle', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('compromisos') as b:
        b.drop_column('cierre_detalle')
        b.drop_column('cierre_veredicto')
    op.drop_index('ix_evidencias_archivo_compromiso_id', 'evidencias_archivo')
    op.drop_table('evidencias_archivo')
    op.drop_index('ix_ediciones_entidad_id', 'ediciones')
    op.drop_index('ix_ediciones_entidad', 'ediciones')
    op.drop_table('ediciones')

"""archivo de compromisos con autorizacion de admin

Revision ID: 9d1f3b7c5e20
Revises: 7c2e4a91d0b3
Create Date: 2026-11-03 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9d1f3b7c5e20'
down_revision: Union[str, None] = '7c2e4a91d0b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('compromisos') as b:
        b.add_column(sa.Column('archivado', sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column('archivado_en', sa.DateTime(), nullable=True))
        b.create_index('ix_compromisos_archivado', ['archivado'])
    op.create_table(
        'solicitudes_eliminacion',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('compromiso_id', sa.Integer(), sa.ForeignKey('compromisos.id', name='fk_solelim_compromiso'), nullable=False),
        sa.Column('motivo', sa.Text(), nullable=False),
        sa.Column('solicitado_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_solelim_solicitante'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.Column('estado', sa.String(length=12), nullable=False),
        sa.Column('resuelto_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_solelim_resolutor'), nullable=True),
        sa.Column('resuelto_en', sa.DateTime(), nullable=True),
        sa.Column('comentario', sa.Text(), nullable=True),
    )
    op.create_index('ix_solicitudes_eliminacion_compromiso_id', 'solicitudes_eliminacion', ['compromiso_id'])
    op.create_index('ix_solicitudes_eliminacion_estado', 'solicitudes_eliminacion', ['estado'])


def downgrade() -> None:
    op.drop_index('ix_solicitudes_eliminacion_estado', 'solicitudes_eliminacion')
    op.drop_index('ix_solicitudes_eliminacion_compromiso_id', 'solicitudes_eliminacion')
    op.drop_table('solicitudes_eliminacion')
    with op.batch_alter_table('compromisos') as b:
        b.drop_index('ix_compromisos_archivado')
        b.drop_column('archivado_en')
        b.drop_column('archivado')

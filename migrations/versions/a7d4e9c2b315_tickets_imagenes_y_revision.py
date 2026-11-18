"""tickets: imágenes dentro del texto y permiso para cerrar o regresar tickets en revisión

Revision ID: a7d4e9c2b315
Revises: e6c2a8d4b791
Create Date: 2026-11-18 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d4e9c2b315'
down_revision: Union[str, None] = 'e6c2a8d4b791'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('revisa_tickets', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'ticket_imagenes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id', name='fk_timg_ticket'), nullable=True),
        sa.Column('ruta', sa.String(300), nullable=False),
        sa.Column('tipo', sa.String(60), nullable=False, server_default='image/png'),
        sa.Column('bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('sha256', sa.String(64), nullable=False, server_default=''),
        sa.Column('subido_por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_timg_usuario'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_imagenes_ticket_id', 'ticket_imagenes', ['ticket_id'])


def downgrade() -> None:
    op.drop_index('ix_ticket_imagenes_ticket_id', table_name='ticket_imagenes')
    op.drop_table('ticket_imagenes')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('revisa_tickets')

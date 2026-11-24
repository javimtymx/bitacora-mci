"""tickets: V°B° cliente, cierres Cancelado/Aclaración, cola y cargas por ingeniero; imágenes en pendientes de clientes

Revision ID: b3e8f1a6c924
Revises: a7d4e9c2b315
Create Date: 2026-11-24 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3e8f1a6c924'
down_revision: Union[str, None] = 'a7d4e9c2b315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUEVOS_CIERRES = (("aclaracion", "Aclaración (no era falla)"), ("cancelado", "Cancelado"))


def upgrade() -> None:
    with op.batch_alter_table('tickets') as b:
        b.add_column(sa.Column('orden_cola', sa.Integer(), nullable=True))
        b.add_column(sa.Column('inicio_plan', sa.Date(), nullable=True))
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('horas_dia', sa.Numeric(4, 1), nullable=True))
    with op.batch_alter_table('ticket_imagenes') as b:
        b.add_column(sa.Column('pendiente_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_timg_pendiente', 'pendientes_cliente', ['pendiente_id'], ['id'])
        b.create_index('ix_ticket_imagenes_pendiente_id', ['pendiente_id'])
    # catálogo «¿Cómo se resolvió?»: si ya se cargó, se agregan las dos opciones nuevas al final
    con = op.get_bind()
    hay = con.execute(sa.text("SELECT COUNT(*) FROM ticket_opciones WHERE catalogo = 'cierre'")).scalar() or 0
    if hay:
        orden = con.execute(sa.text("SELECT COALESCE(MAX(orden), 0) FROM ticket_opciones WHERE catalogo = 'cierre'")).scalar() or 0
        for clave, etiqueta in NUEVOS_CIERRES:
            existe = con.execute(sa.text("SELECT COUNT(*) FROM ticket_opciones WHERE catalogo = 'cierre' AND clave = :c"),
                                 {"c": clave}).scalar()
            if not existe:
                orden += 1
                con.execute(sa.text("INSERT INTO ticket_opciones (catalogo, clave, etiqueta, orden, activo, menor, creado) "
                                    "VALUES ('cierre', :c, :e, :o, :a, :m, CURRENT_TIMESTAMP)"), {"c": clave, "e": etiqueta, "o": orden,
                                                                               "a": True, "m": False})


def downgrade() -> None:
    con = op.get_bind()
    if con.execute(sa.text("SELECT COUNT(*) FROM tickets WHERE estado = 'vobo'")).scalar():
        con.execute(sa.text("UPDATE tickets SET estado = 'revision' WHERE estado = 'vobo'"))
    with op.batch_alter_table('ticket_imagenes') as b:
        b.drop_index('ix_ticket_imagenes_pendiente_id')
        b.drop_constraint('fk_timg_pendiente', type_='foreignkey')
        b.drop_column('pendiente_id')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('horas_dia')
    with op.batch_alter_table('tickets') as b:
        b.drop_column('inicio_plan')
        b.drop_column('orden_cola')

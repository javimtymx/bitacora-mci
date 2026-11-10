"""tickets: catálogo de área del usuario e historia de las estimaciones de horas

Solo agrega. Arranca el catálogo de áreas con las áreas que ya existen, asigna a cada usuario la de su área actual
y deja la planeación vigente de cada ticket como su versión 1 (estimación original).

Revision ID: e1f5b9c3a742
Revises: d8b1f4a6c290
Create Date: 2026-11-10 16:30:00

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f5b9c3a742'
down_revision: Union[str, None] = 'd8b1f4a6c290'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('usuarios') as b:
        b.add_column(sa.Column('area_ticket', sa.String(40), nullable=True))
    with op.batch_alter_table('tickets') as b:
        b.add_column(sa.Column('horas_originales', sa.Numeric(8, 2), nullable=True))
        b.add_column(sa.Column('area', sa.String(40), nullable=True))
    op.create_table(
        'ticket_estimaciones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id', name='fk_testim_ticket'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('horas', sa.Numeric(8, 2), nullable=False),
        sa.Column('fecha_entrega', sa.Date(), nullable=True),
        sa.Column('asignado_id', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_testim_asignado'), nullable=True),
        sa.Column('motivo', sa.Text(), nullable=True),
        sa.Column('por', sa.Integer(), sa.ForeignKey('usuarios.id', name='fk_testim_por'), nullable=False),
        sa.Column('creado', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_ticket_estimaciones_ticket_id', 'ticket_estimaciones', ['ticket_id'])

    con = op.get_bind()
    # catálogo de áreas del usuario: se arranca con las áreas actuales
    ya = {r[0] for r in con.execute(sa.text("SELECT clave FROM ticket_opciones WHERE catalogo = 'area'"))}
    areas = list(con.execute(sa.text("SELECT id, clave, nombre, activo FROM areas ORDER BY id")))
    for i, (_, clave, nombre, activo) in enumerate(areas, 1):
        if clave not in ya:
            con.execute(sa.text("INSERT INTO ticket_opciones (catalogo, clave, etiqueta, orden, activo, menor, creado) "
                                "VALUES ('area', :c, :e, :o, :a, :m, :t)"),
                        {"c": clave[:40], "e": nombre[:120], "o": i, "a": bool(activo), "m": False, "t": datetime.utcnow()})
    for aid, clave, _, _ in areas:
        con.execute(sa.text("UPDATE usuarios SET area_ticket = :c WHERE area_id = :a AND area_ticket IS NULL"),
                    {"c": clave, "a": aid})
    con.execute(sa.text("UPDATE tickets SET area = (SELECT u.area_ticket FROM usuarios u WHERE u.id = tickets.creado_por) "
                        "WHERE area IS NULL"))
    # la planeación vigente queda como estimación original (versión 1)
    filas = list(con.execute(sa.text("SELECT id, horas_estimadas, fecha_entrega, asignado_id, creado_por, planeado_en "
                                     "FROM tickets WHERE horas_estimadas IS NOT NULL")))
    for tid, horas, entrega, asignado, autor, planeado in filas:
        con.execute(sa.text("INSERT INTO ticket_estimaciones (ticket_id, version, horas, fecha_entrega, asignado_id, motivo, "
                            "por, creado) VALUES (:t, 1, :h, :f, :a, '', :p, :c)"),
                    {"t": tid, "h": horas, "f": entrega, "a": asignado, "p": asignado or autor,
                     "c": planeado or datetime.utcnow()})
        con.execute(sa.text("UPDATE tickets SET horas_originales = :h WHERE id = :t"), {"h": horas, "t": tid})


def downgrade() -> None:
    op.drop_index('ix_ticket_estimaciones_ticket_id', table_name='ticket_estimaciones')
    op.drop_table('ticket_estimaciones')
    op.execute("DELETE FROM ticket_opciones WHERE catalogo = 'area'")
    with op.batch_alter_table('tickets') as b:
        b.drop_column('area')
        b.drop_column('horas_originales')
    with op.batch_alter_table('usuarios') as b:
        b.drop_column('area_ticket')

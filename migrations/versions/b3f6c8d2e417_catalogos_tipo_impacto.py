"""catalogos editables de tipo e impacto de tickets

Revision ID: b3f6c8d2e417
Revises: a1d9e3c7b542
Create Date: 2026-11-09 20:30:00

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f6c8d2e417'
down_revision: Union[str, None] = 'a1d9e3c7b542'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    t = op.create_table(
        'ticket_opciones',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('catalogo', sa.String(length=12), nullable=False),
        sa.Column('clave', sa.String(length=40), nullable=False),
        sa.Column('etiqueta', sa.String(length=120), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('prioridad', sa.String(length=2), nullable=True),
        sa.Column('menor', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('creado', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('catalogo', 'clave', name='uq_ticket_opcion'),
    )
    ahora = datetime.utcnow()
    filas = [("tipo", "error", "Error", 1, None, False), ("tipo", "duda", "Duda", 2, None, True),
             ("tipo", "mejora", "Mejora", 3, None, True), ("tipo", "configuracion", "Configuración", 4, None, False),
             ("impacto", "bloquea", "Bloquea la operación", 1, "P1", False),
             ("impacto", "afecta", "Afecta pero hay forma de seguir", 2, "P2", False),
             ("impacto", "menor", "Molestia menor", 3, "P3", False)]
    op.bulk_insert(t, [{"catalogo": c, "clave": k, "etiqueta": e, "orden": o, "activo": True, "prioridad": p,
                        "menor": m, "creado": ahora} for c, k, e, o, p, m in filas])


def downgrade() -> None:
    op.drop_table('ticket_opciones')

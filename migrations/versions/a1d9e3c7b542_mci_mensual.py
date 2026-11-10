"""MCI con valor mensual: la última semana capturada de cada mes pasa a ser el valor del mes

Solo AGREGA filas: no modifica ni borra ninguna medición existente (semanales de MCI, predictivas, compromisos).
Si un mes ya tiene valor mensual, no se toca.

Revision ID: a1d9e3c7b542
Revises: f2a7c5e1b984
Create Date: 2026-11-09 20:00:00

"""
from datetime import date, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1d9e3c7b542'
down_revision: Union[str, None] = 'f2a7c5e1b984'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARCA = "Tomado de la semana "


def _mes(semana: str) -> str:
    y, w = semana.split("-W")
    return date.fromisocalendar(int(y), int(w), 5).strftime("%Y-%m")  # la semana pertenece al mes de su viernes


def upgrade() -> None:
    conn = op.get_bind()
    filas = conn.execute(sa.text(
        "SELECT m.id, m.version_id, m.periodo, m.valor, m.fuente, m.nota, m.capturado_por, v.mci_id "
        "FROM mediciones m JOIN mci_versiones v ON v.id = m.version_id "
        "WHERE m.tipo = 'mci' AND m.periodo LIKE '%-W%'")).fetchall()
    mensuales = {(r.mci_id, r.periodo) for r in conn.execute(sa.text(
        "SELECT v.mci_id, m.periodo FROM mediciones m JOIN mci_versiones v ON v.id = m.version_id "
        "WHERE m.tipo = 'mci' AND m.periodo NOT LIKE '%-W%'")).fetchall()}
    ultima: dict[tuple[int, str], object] = {}
    for r in filas:
        try:
            clave = (r.mci_id, _mes(r.periodo))
        except (ValueError, IndexError):
            continue
        if clave not in ultima or r.periodo > ultima[clave].periodo:
            ultima[clave] = r
    tabla = sa.table("mediciones", sa.column("tipo"), sa.column("version_id"), sa.column("periodo"), sa.column("valor"),
                     sa.column("fuente"), sa.column("nota"), sa.column("capturado_por"), sa.column("creado"))
    nuevas = []
    for (mci_id, mes), r in sorted(ultima.items()):
        if (mci_id, mes) in mensuales:
            continue
        nota = f"{MARCA}{r.periodo}" + (f" · {r.nota}" if r.nota else "")
        nuevas.append({"tipo": "mci", "version_id": r.version_id, "periodo": mes, "valor": r.valor,
                       "fuente": r.fuente or "captura", "nota": nota[:2000], "capturado_por": r.capturado_por,
                       "creado": datetime.utcnow()})
    if nuevas:
        op.bulk_insert(tabla, nuevas)


def downgrade() -> None:
    op.get_bind().execute(sa.text(f"DELETE FROM mediciones WHERE tipo = 'mci' AND periodo NOT LIKE '%-W%' "
                                  f"AND nota LIKE '{MARCA}%'"))

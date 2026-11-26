"""pendientes de clientes enviados a soporte que no se movieron a «Delegado a soporte» (datos, sin cambio de esquema)

Antes el pendiente pasaba a «Delegado» hasta que soporte aceptaba el ticket en triage, y el triage automático no lo
movía. Ahora se mueve al enviarlo; esta migración pone al día los que ya estaban enviados, dejando nota en su bitácora.

Revision ID: c9d2a4e7f310
Revises: b3e8f1a6c924
Create Date: 2026-11-26 10:00:00

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d2a4e7f310'
down_revision: Union[str, None] = 'b3e8f1a6c924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ABIERTOS = ("nuevo", "pendiente", "planeado", "en_curso", "info", "revision", "vobo")


def upgrade() -> None:
    con = op.get_bind()
    pend = sa.table("pendientes_cliente", sa.column("id", sa.Integer), sa.column("estado", sa.String),
                    sa.column("historial", sa.JSON), sa.column("ticket_id", sa.Integer))
    tick = sa.table("tickets", sa.column("id", sa.Integer), sa.column("estado", sa.String))
    filas = con.execute(sa.select(pend.c.id, pend.c.estado, pend.c.historial, tick.c.estado, tick.c.id)
                        .select_from(pend.join(tick, pend.c.ticket_id == tick.c.id))
                        .where(pend.c.estado.in_(("propuesta", "pendiente", "en_curso")))).all()
    ahora = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    for pid, est, hist, test, tid in filas:
        folio = f"ONE-{tid}"
        if test in ABIERTOS:
            nuevo, texto = "delegado", f"Puesto al día: ya estaba enviado a soporte como {folio}; queda «Delegado a soporte»."
        elif test == "cerrado":
            nuevo, texto = "revision", f"Puesto al día: soporte ya cerró {folio}; regresa a revisión para confirmarlo con el cliente."
        else:
            continue
        hist = list(hist or []) + [{"en": ahora, "por": "GuardIA", "texto": texto, "ticket": test}]
        con.execute(pend.update().where(pend.c.id == pid).values(estado=nuevo, historial=hist))


def downgrade() -> None:
    pass  # solo pone al día estados; no hay nada que revertir

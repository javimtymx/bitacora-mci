"""Tareas programadas del PM virtual (las dispara systemd; ver deploy/).

    python -m app.jobs informe-diario      # 7:30 L-V
    python -m app.jobs recordatorios       # 8:00 L-V
    python -m app.jobs ejecutar-acciones   # cada 15 min
    python -m app.jobs sync-cfdi           # 6:00 diario
"""
from __future__ import annotations

import logging
import sys

from .db import SessionLocal
from .services import acciones, agente, cfdi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bitacora.jobs")

TAREAS = {
    "informe-diario": lambda db: f"informe {agente.informe_diario(db).id}",
    "recordatorios": lambda db: f"{agente.recordatorios(db)} recordatorios",
    "ejecutar-acciones": lambda db: f"{acciones.ejecutar_pendientes(db)} acciones ejecutadas",
    "sync-cfdi": lambda db: f"{cfdi.sincronizar(db)} comprobantes leídos",
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in TAREAS:
        print(__doc__)
        return 2
    tarea = sys.argv[1]
    with SessionLocal() as db:
        try:
            log.info("%s: %s", tarea, TAREAS[tarea](db))
        except Exception:
            log.exception("%s falló", tarea)
            db.rollback()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

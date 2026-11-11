"""Tareas programadas de GuardIA OneCore (las dispara systemd; ver deploy/).

    python -m app.jobs informe-diario      # 7:30 L-V
    python -m app.jobs recordatorios       # 8:00 L-V
    python -m app.jobs ejecutar-acciones   # cada 15 min
    python -m app.jobs sync-cfdi           # 6:00 diario
    python -m app.jobs diagnostico-semanal # lunes 7:00
"""
from __future__ import annotations

import logging
import sys

from .db import SessionLocal
from .services import acciones, agente, agenda_avisos, cfdi, diagnostico, parametros

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bitacora.jobs")

TAREAS = {
    "informe-diario": lambda db: f"informe {agente.informe_diario(db).id}",
    "recordatorios": lambda db: (f"{agente.recordatorios(db)} recordatorios; "
                                 f"{agenda_avisos.recordatorios(db)} recordatorios de sesiones con cliente; "
                                 f"{agenda_avisos.falta_transcripcion(db)} transcripciones pendientes"),
    "ejecutar-acciones": lambda db: f"{acciones.ejecutar_pendientes(db)} acciones ejecutadas",
    "sync-cfdi": lambda db: f"{cfdi.sincronizar(db)} comprobantes leídos",
    "diagnostico-semanal": lambda db: (f"{diagnostico.semanal_todas(db)} diagnósticos generados"
                                       if parametros.get(db, "auto_diagnostico_semanal") else "desactivado en parámetros"),
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

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Auditoria

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def hoy() -> date:
    return datetime.now(ZoneInfo(get_settings().zona_horaria)).date()


def semana_iso(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def inicio_seguimiento() -> str:
    """Semana ISO desde la que cuentan los cálculos (INICIO_SEGUIMIENTO, p. ej. 2026-W46)."""
    from ..config import get_settings
    return get_settings().inicio_seguimiento or "2000-W01"


def semanas_atras(n: int, desde: date | None = None) -> list[str]:
    """Últimas n semanas hasta `desde`, sin las anteriores al inicio del seguimiento (al menos la semana actual)."""
    d = desde or hoy()
    sems = [semana_iso(d - timedelta(weeks=i)) for i in range(n)]
    ini = inicio_seguimiento()
    return [s for s in sems if s >= ini] or sems[:1]


def num_semana(s: str) -> int:
    try:
        return int(s.split("-W")[1])
    except (IndexError, ValueError):
        return 0


def viernes(d: date | None = None) -> date:
    d = d or hoy()
    return d + timedelta(days=(4 - d.weekday()) % 7)


def rango_semana(d: date | None = None) -> tuple[date, date]:
    d = d or hoy()
    lunes = d - timedelta(days=d.weekday())
    return lunes, lunes + timedelta(days=4)


def fmt_dia(d: date | datetime | None) -> str:
    if not d:
        return "—"
    if isinstance(d, datetime):
        d = d.date()
    return f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"


def fmt_fecha(d: date | datetime | None) -> str:
    if not d:
        return "—"
    return d.strftime("%d/%m/%Y")


def dias_desde(dt: datetime | date | None) -> int | None:
    if not dt:
        return None
    d = dt.date() if isinstance(dt, datetime) else dt
    return (hoy() - d).days


def auditar(db: Session, accion: str, entidad: str, entidad_id: int | None = None,
            usuario_id: int | None = None, actor: str = "usuario", **detalle) -> None:
    db.add(Auditoria(usuario_id=usuario_id, actor=actor, accion=accion, entidad=entidad,
                     entidad_id=entidad_id, detalle=detalle))

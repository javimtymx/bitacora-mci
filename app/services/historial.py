"""Historial de ediciones de compromisos e ideas (tracking de qué se cambió)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Edicion

CAMPOS = {
    "compromiso": {"texto": "Compromiso", "fecha_limite": "Fecha límite", "responsable_id": "Responsable",
                   "predictiva_id": "Medida predictiva"},
    "idea": {"titulo": "Título", "problema": "Problema", "beneficio": "Beneficio", "area_id": "Área"},
}


def _v(x):
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    return x


def foto(obj, entidad: str) -> dict:
    return {k: _v(getattr(obj, k)) for k in CAMPOS[entidad]}


def registrar(db: Session, entidad: str, entidad_id: int, usuario_id: int, antes: dict, despues: dict,
              motivo: str = "") -> Edicion | None:
    """Guarda solo los campos que cambiaron. Regresa None si no hubo cambios."""
    a = {k: v for k, v in antes.items() if despues.get(k) != v}
    d = {k: despues[k] for k in a}
    if not a:
        return None
    e = Edicion(entidad=entidad, entidad_id=entidad_id, usuario_id=usuario_id, antes=a, despues=d,
                motivo=(motivo or "").strip()[:1000])
    db.add(e)
    return e


def de(db: Session, entidad: str, entidad_id: int) -> list[Edicion]:
    return list(db.scalars(select(Edicion).where(Edicion.entidad == entidad, Edicion.entidad_id == entidad_id)
                           .order_by(Edicion.creado.desc())))


def conteos(db: Session, entidad: str, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    out: dict[int, int] = {}
    for (eid,) in db.execute(select(Edicion.entidad_id).where(Edicion.entidad == entidad, Edicion.entidad_id.in_(ids))):
        out[eid] = out.get(eid, 0) + 1
    return out

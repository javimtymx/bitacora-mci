"""Catálogo estratégico versionado: MCI, medidas predictivas y estrategias."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ESTADOS_ESTRATEGIA,
    Estrategia,
    EstrategiaEvento,
    Mci,
    MciVersion,
    Predictiva,
    PredictivaVersion,
)
from .util import auditar, hoy


def version_vigente(versiones, fecha: date | None = None):
    fecha = fecha or hoy()
    vig = [v for v in versiones if v.vigente_desde <= fecha and (v.vigente_hasta is None or v.vigente_hasta >= fecha)]
    if vig:
        return max(vig, key=lambda v: v.version)
    return versiones[-1] if versiones else None


def nueva_version_predictiva(db: Session, pred: Predictiva, *, descripcion: str, meta_semanal, unidad: str,
                             sentido: str, fuente: str, vigente_desde: date, motivo: str,
                             usuario_id: int | None) -> PredictivaVersion:
    actual = pred.versiones[-1] if pred.versiones else None
    if actual and actual.vigente_hasta is None:
        actual.vigente_hasta = vigente_desde - timedelta(days=1)
    v = PredictivaVersion(predictiva_id=pred.id, version=(actual.version + 1) if actual else 1,
                          descripcion=descripcion, meta_semanal=meta_semanal, unidad=unidad, sentido=sentido,
                          fuente=fuente, vigente_desde=vigente_desde, motivo_cambio=motivo, creado_por=usuario_id)
    db.add(v)
    auditar(db, "nueva_version", "predictiva", pred.id, usuario_id, motivo=motivo, version=v.version)
    return v


def nueva_version_mci(db: Session, mci: Mci, *, descripcion: str, meta, unidad: str, sentido: str,
                      periodicidad: str, vigente_desde: date, motivo: str, usuario_id: int | None) -> MciVersion:
    actual = mci.versiones[-1] if mci.versiones else None
    if actual and actual.vigente_hasta is None:
        actual.vigente_hasta = vigente_desde - timedelta(days=1)
    v = MciVersion(mci_id=mci.id, version=(actual.version + 1) if actual else 1, descripcion=descripcion,
                   meta=meta, unidad=unidad, sentido=sentido, periodicidad=periodicidad,
                   vigente_desde=vigente_desde, motivo_cambio=motivo, creado_por=usuario_id)
    db.add(v)
    auditar(db, "nueva_version", "mci", mci.id, usuario_id, motivo=motivo, version=v.version)
    return v


def cambiar_estado_estrategia(db: Session, est: Estrategia, nuevo: str, motivo: str, usuario_id: int | None,
                              evidencia: str | None = None) -> None:
    if nuevo not in ESTADOS_ESTRATEGIA:
        raise ValueError(nuevo)
    if not motivo.strip():
        raise ValueError("El cambio de estado requiere un motivo.")
    anterior = est.estado
    est.estado = nuevo
    if nuevo == "activa" and not est.inicio:
        est.inicio = hoy()
    if nuevo in ("descartada", "exitosa") and not est.fin:
        est.fin = hoy()
    db.add(EstrategiaEvento(estrategia_id=est.id, tipo="cambio_estado", estado_anterior=anterior,
                            estado_nuevo=nuevo, motivo=motivo, evidencia=evidencia, usuario_id=usuario_id))
    auditar(db, "cambio_estado", "estrategia", est.id, usuario_id, de=anterior, a=nuevo, motivo=motivo)


def predictivas_por_area(db: Session) -> dict[int, list[tuple[Predictiva, PredictivaVersion]]]:
    out: dict[int, list] = {}
    for p in db.scalars(select(Predictiva).where(Predictiva.activo.is_(True))):
        v = version_vigente(p.versiones)
        if v:
            out.setdefault(p.mci.area_id, []).append((p, v))
    return out

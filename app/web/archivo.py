"""Archivo de compromisos: cualquiera solicita la eliminación con su causa; solo un admin la autoriza.

Mientras la solicitud está pendiente el compromiso sigue visible (marcado). Al autorizarla, el compromiso y su
seguimiento (remediación y acciones programadas) pasan al archivo: dejan de contar en indicadores, reportes y la IA,
pero no se borran de la base de datos, para conservar la trazabilidad.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AccionAgente, Compromiso, SolicitudEliminacion, Usuario, utcnow
from ..services.util import auditar
from .base import flash, ir, render, usuario_actual

router = APIRouter()
TODOS = {"include_archivados": True}


def requiere_admin(u: Usuario = Depends(usuario_actual)) -> Usuario:
    if u.rol != "admin":
        raise HTTPException(403, "Solo un administrador puede autorizar eliminaciones.")
    return u


def _visible(u: Usuario, c: Compromiso) -> bool:
    return u.es_direccion or c.area_id == u.area_id or c.responsable_id == u.id


def pendientes(db: Session) -> int:
    return db.scalar(select(func.count(SolicitudEliminacion.id)).where(SolicitudEliminacion.estado == "pendiente")) or 0


def solicitud_pendiente(db: Session, cid: int) -> SolicitudEliminacion | None:
    return db.scalar(select(SolicitudEliminacion).where(SolicitudEliminacion.compromiso_id == cid,
                                                        SolicitudEliminacion.estado == "pendiente").limit(1))


@router.post("/compromisos/{cid}/eliminar")
def solicitar(cid: int, request: Request, motivo: str = Form(...), db: Session = Depends(get_db),
              u: Usuario = Depends(usuario_actual)):
    c = db.get(Compromiso, cid)
    if not c or not _visible(u, c):
        raise HTTPException(404)
    motivo = motivo.strip()
    if len(motivo) < 10:
        flash(request, "Escribe la causa de la eliminación (al menos 10 caracteres).", "error")
        return ir(f"/compromisos/{cid}")
    if solicitud_pendiente(db, cid):
        flash(request, "Ya hay una solicitud de eliminación pendiente para este compromiso.", "error")
        return ir(f"/compromisos/{cid}")
    s = SolicitudEliminacion(compromiso_id=cid, motivo=motivo[:1000], solicitado_por=u.id)
    db.add(s)
    db.flush()
    auditar(db, "solicitar_eliminacion", "compromiso", cid, u.id, motivo=motivo[:300])
    db.commit()
    flash(request, "Solicitud enviada. El compromiso sigue visible hasta que un administrador la autorice.")
    return ir(f"/compromisos/{cid}")


@router.get("/compromisos/eliminaciones")
def informe(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_admin)):
    base = select(SolicitudEliminacion).execution_options(**TODOS)
    pend = list(db.scalars(base.where(SolicitudEliminacion.estado == "pendiente").order_by(SolicitudEliminacion.creado)))
    resueltas = list(db.scalars(base.where(SolicitudEliminacion.estado != "pendiente")
                                .order_by(SolicitudEliminacion.resuelto_en.desc()).limit(30)))
    from ..services import seguimiento
    return render(request, "eliminaciones.html", u, "compromisos", pend=pend, resueltas=resueltas, seg=seguimiento)


def _resolver(db: Session, sid: int) -> SolicitudEliminacion:
    s = db.get(SolicitudEliminacion, sid)
    if not s:
        raise HTTPException(404)
    if s.estado != "pendiente":
        raise HTTPException(400, "Esta solicitud ya fue resuelta.")
    return s


@router.post("/compromisos/eliminaciones/{sid}/aprobar")
def aprobar(sid: int, request: Request, comentario: str = Form(""), db: Session = Depends(get_db),
            u: Usuario = Depends(requiere_admin)):
    s = _resolver(db, sid)
    c = db.get(Compromiso, s.compromiso_id, execution_options=TODOS)
    s.estado, s.resuelto_por, s.resuelto_en, s.comentario = "aprobada", u.id, utcnow(), comentario.strip()[:1000] or None
    c.archivado, c.archivado_en = True, utcnow()
    # su seguimiento también se va al archivo: las acciones que aún no salen se descartan
    n = 0
    for a in db.scalars(select(AccionAgente).where(AccionAgente.entidad_tipo == "compromiso", AccionAgente.entidad_id == c.id,
                                                   AccionAgente.estado.in_(("propuesta", "aprobada", "fallida")))):
        a.estado = "rechazada"
        n += 1
    auditar(db, "aprobar_eliminacion", "compromiso", c.id, u.id, solicitud=s.id, acciones_descartadas=n)
    db.commit()
    flash(request, f"CMP-{c.id:04d} se envió al archivo" + (f" y se descartaron {n} acciones de seguimiento." if n else "."))
    return ir("/compromisos/eliminaciones")


@router.post("/compromisos/eliminaciones/{sid}/rechazar")
def rechazar(sid: int, request: Request, comentario: str = Form(...), db: Session = Depends(get_db),
             u: Usuario = Depends(requiere_admin)):
    s = _resolver(db, sid)
    if len(comentario.strip()) < 5:
        flash(request, "Explica por qué se rechaza la eliminación.", "error")
        return ir("/compromisos/eliminaciones")
    s.estado, s.resuelto_por, s.resuelto_en, s.comentario = "rechazada", u.id, utcnow(), comentario.strip()[:1000]
    auditar(db, "rechazar_eliminacion", "compromiso", s.compromiso_id, u.id, solicitud=s.id)
    db.commit()
    flash(request, f"Se rechazó la eliminación de CMP-{s.compromiso_id:04d}; el compromiso sigue activo.")
    return ir("/compromisos/eliminaciones")


def archivo(db: Session, u: Usuario, area_id: int | None = None) -> list[tuple[Compromiso, str]]:
    """Archivo = eliminados con autorización de un admin + cancelados (p. ej., por baja del responsable)."""
    from sqlalchemy import or_
    q = select(Compromiso).execution_options(**TODOS).where(or_(Compromiso.archivado.is_(True),
                                                                Compromiso.estado == "cancelado"))
    if area_id:
        q = q.where(Compromiso.area_id == area_id)
    out = [(c, "eliminado" if c.archivado else "cancelado") for c in db.scalars(q) if _visible(u, c)]
    return sorted(out, key=lambda x: x[0].archivado_en or x[0].cerrado or x[0].creado, reverse=True)


@router.get("/compromisos/archivados")
def archivados(request: Request, area: str = "", tipo: str = "", db: Session = Depends(get_db),
               u: Usuario = Depends(usuario_actual)):
    from ..models import Area
    a = db.scalar(select(Area).where(Area.clave == area)) if area else None
    todos = archivo(db, u, a.id if a else None)
    lista = [x for x in todos if not tipo or x[1] == tipo]
    sol = {s.compromiso_id: s for s in db.scalars(select(SolicitudEliminacion).execution_options(**TODOS)
                                                  .where(SolicitudEliminacion.estado == "aprobada"))}
    areas = list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)))
    return render(request, "archivados.html", u, "compromisos", lista=lista, sol=sol, area=a, areas=areas, tipo=tipo,
                  n_elim=sum(1 for x in todos if x[1] == "eliminado"), n_canc=sum(1 for x in todos if x[1] == "cancelado"))


# ---------------------------------------------------------------- subida directa a S3 (archivos grandes)

@router.post("/api/subidas/firmar")
async def firmar_subida(request: Request, u: Usuario = Depends(usuario_actual)):
    from fastapi.responses import JSONResponse
    from ..services import subidas
    if not subidas.disponible():
        return JSONResponse({"ok": False, "sin_s3": True}, status_code=409)
    try:
        d = await request.json()
        r = subidas.firmar(u.id, str(d.get("destino") or ""), str(d.get("nombre") or ""), int(d.get("tamano") or 0))
    except (subidas.SubidaInvalida, ValueError) as e:
        return JSONResponse({"ok": False, "mensaje": str(e)}, status_code=400)
    return JSONResponse({"ok": True, **r})

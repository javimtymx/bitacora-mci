"""Acciones del agente: correos, recordatorios e informes.

Ciclo: propuesta → aprobada → ejecutada (o rechazada / fallida).
Las acciones de bajo riesgo pueden nacer aprobadas si su parámetro de automatización
está activo; todo lo demás espera la aprobación de un líder o de Dirección."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AccionAgente, Compromiso, Informe, Usuario, utcnow
from . import correo, ia
from .util import auditar


def crear(db: Session, *, tipo: str, origen: str, titulo: str, destinatarios: list[int], asunto: str = "",
          cuerpo: str = "", instrucciones: str | None = None, entidad_tipo: str | None = None,
          entidad_id: int | None = None, programada_para: datetime | None = None, auto: bool = False,
          creado_por: int | None = None) -> AccionAgente:
    a = AccionAgente(tipo=tipo, origen=origen, titulo=titulo[:200], destinatarios=sorted(set(destinatarios)),
                     asunto=asunto[:200], cuerpo=cuerpo, instrucciones=instrucciones, entidad_tipo=entidad_tipo,
                     entidad_id=entidad_id, programada_para=programada_para or utcnow(),
                     estado="aprobada" if auto else "propuesta", requiere_aprobacion=not auto,
                     creado_por=creado_por)
    db.add(a)
    db.flush()
    auditar(db, "accion_creada", "accion", a.id, creado_por, actor="agente" if creado_por is None else "usuario",
            tipo=tipo, origen=origen, auto=auto)
    return a


def ya_existe(db: Session, origen: str, entidad_tipo: str, entidad_id: int, desde: datetime) -> bool:
    return db.scalar(select(AccionAgente.id).where(AccionAgente.origen == origen,
                                                   AccionAgente.entidad_tipo == entidad_tipo,
                                                   AccionAgente.entidad_id == entidad_id,
                                                   AccionAgente.creado >= desde).limit(1)) is not None


def aprobar(db: Session, a: AccionAgente, usuario: Usuario) -> None:
    a.estado = "aprobada"
    a.aprobada_por = usuario.id
    a.aprobada_en = utcnow()
    auditar(db, "accion_aprobada", "accion", a.id, usuario.id)


def rechazar(db: Session, a: AccionAgente, usuario: Usuario) -> None:
    a.estado = "rechazada"
    a.aprobada_por = usuario.id
    a.aprobada_en = utcnow()
    auditar(db, "accion_rechazada", "accion", a.id, usuario.id)


def lideres_de(db: Session, area_id: int) -> list[int]:
    return [u.id for u in db.scalars(select(Usuario).where(Usuario.activo.is_(True), Usuario.area_id == area_id,
                                                           Usuario.rol == "lider"))]


def direccion(db: Session) -> list[int]:
    return [u.id for u in db.scalars(select(Usuario).where(Usuario.activo.is_(True), Usuario.rol.in_(("direccion", "admin"))))]


def resolver_para(db: Session, para: str, c: Compromiso) -> list[int]:
    if para == "lider":
        return lideres_de(db, c.area_id) or direccion(db)
    if para == "direccion":
        return direccion(db)
    return [c.responsable_id]


def contexto_entidad(db: Session, a: AccionAgente) -> str:
    if a.entidad_tipo == "compromiso" and a.entidad_id:
        c = db.get(Compromiso, a.entidad_id)
        if c:
            r = c.remediacion
            return (f"Área: {c.area.nombre}\nResponsable: {c.responsable.nombre}\nCompromiso: {c.texto}\n"
                    f"Estado: {c.estado}\nFecha límite: {c.fecha_limite}\n"
                    + (f"Causa: {r.causa}\nRemediación: {r.accion}\nNueva fecha: {r.nueva_fecha}\n" if r else ""))
    return ""


def ejecutar(db: Session, a: AccionAgente) -> None:
    s = get_settings()
    users = [db.get(Usuario, i) for i in a.destinatarios]
    emails = [u.email for u in users if u and u.activo and u.recibe_correos]
    try:
        if a.tipo == "informe":
            res, meta = ia.redactar_informe(db, a.titulo, a.instrucciones or a.cuerpo, contexto_entidad(db, a))
            inf = Informe(tipo="remediacion" if a.entidad_tipo == "compromiso" else "general",
                          titulo=str(res.get("titulo") or a.titulo)[:200],
                          contenido=str(res.get("contenido_markdown") or ""), origen="agente",
                          datos={"accion_id": a.id, "modelo": meta.get("modelo")})
            db.add(inf)
            db.flush()
            a.informe_id = inf.id
            if emails:
                correo.enviar(emails, a.asunto or f"Informe listo: {inf.titulo}",
                              f"El PM virtual preparó el documento «{inf.titulo}». Revísalo y complétalo donde diga [pendiente].",
                              f"{s.base_url}/informes/{inf.id}")
        else:
            correo.enviar(emails, a.asunto or a.titulo, a.cuerpo,
                          f"{s.base_url}/compromisos/{a.entidad_id}" if a.entidad_tipo == "compromiso" else s.base_url)
        a.estado = "ejecutada"
        a.ejecutada_en = utcnow()
        a.error = None
    except Exception as e:  # noqa: BLE001 - se registra para revisión humana
        a.estado = "fallida"
        a.error = str(e)[:1000]
    auditar(db, "accion_ejecutada", "accion", a.id, None, actor="agente", estado=a.estado)


def ejecutar_pendientes(db: Session) -> int:
    ahora = utcnow()
    pend = list(db.scalars(select(AccionAgente).where(AccionAgente.estado == "aprobada",
                                                      AccionAgente.programada_para <= ahora)))
    for a in pend:
        ejecutar(db, a)
        db.commit()
    return len(pend)


def plan_desde_revision(db: Session, c: Compromiso, plan: list[dict], usuario: Usuario, auto: bool,
                        seleccion: list[int] | None = None) -> list[AccionAgente]:
    out = []
    for i, p in enumerate(plan):
        if seleccion is not None and i not in seleccion:
            continue
        cuando = datetime.combine(utcnow().date() + timedelta(days=int(p.get("dias", 0))),
                                  datetime.min.time()) + timedelta(hours=14)  # ~8:00 Ciudad de México
        out.append(crear(db, tipo=p["tipo"], origen="plan_remediacion", titulo=p.get("titulo") or "Seguimiento de remediación",
                         destinatarios=resolver_para(db, p.get("para", "responsable"), c), asunto=p.get("asunto", ""),
                         cuerpo=p.get("cuerpo", ""), instrucciones=p.get("instrucciones") or None,
                         entidad_tipo="compromiso", entidad_id=c.id, programada_para=cuando, auto=auto,
                         creado_por=usuario.id))
    return out

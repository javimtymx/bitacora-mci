"""PM virtual, acciones del agente, informes, estrategias y excepciones."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    ESTADOS_ESTRATEGIA,
    AccionAgente,
    Area,
    Compromiso,
    Estrategia,
    EstrategiaEvento,
    Excepcion,
    Informe,
    Mci,
    Predictiva,
    RevisionIA,
    Usuario,
    utcnow,
)
from ..services import acciones, agente, catalogo, ia, seguimiento
from ..services.util import auditar
from .base import flash, ir, render, requiere_direccion, requiere_lider, usuario_actual, OptInt, OptFloat

router = APIRouter()


# ---------------------------------------------------------------- PM virtual

@router.get("/pm")
def pm(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    area = None if u.es_direccion else u.area_id
    informe = db.scalar(select(Informe).where(Informe.tipo == "pm_diario").order_by(Informe.creado.desc()).limit(1))
    q = select(AccionAgente).where(AccionAgente.estado.in_(("propuesta", "aprobada", "fallida")))
    pendientes = list(db.scalars(q.order_by(AccionAgente.programada_para)))
    if area:
        ids_area = {c for c in db.scalars(select(Compromiso.id).where(Compromiso.area_id == area))}
        pendientes = [a for a in pendientes if a.entidad_tipo != "compromiso" or a.entidad_id in ids_area]
    recientes = list(db.scalars(select(AccionAgente).where(AccionAgente.estado.in_(("ejecutada", "rechazada")))
                                .order_by(AccionAgente.creado.desc()).limit(15)))
    nombres = {x.id: x.nombre for x in db.scalars(select(Usuario))}
    informes = list(db.scalars(select(Informe).where(Informe.tipo != "pm_diario").order_by(Informe.creado.desc()).limit(10)))
    acept = seguimiento.aceptacion_ia(db)
    return render(request, "pm.html", u, "pm", informe=informe, alertas=seguimiento.alertas(db, area),
                  personas=seguimiento.estado_personas(db, area), pendientes=pendientes, recientes=recientes,
                  nombres=nombres, informes=informes, acept=acept)


@router.post("/pm/informe")
def pm_generar(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    from datetime import timedelta
    reciente = db.scalar(select(Informe).where(Informe.tipo == "pm_diario", Informe.creado >= utcnow() - timedelta(minutes=2))
                         .order_by(Informe.creado.desc()).limit(1))
    if reciente:  # doble clic u otra pestaña: no se gasta otra llamada a la IA
        flash(request, "El informe se acaba de generar; te mostramos ese.")
        return ir("/pm")
    inf = agente.informe_diario(db)
    inf.generado_por = u.id
    db.commit()
    flash(request, "El PM virtual generó un informe nuevo.")
    return ir("/pm")


@router.post("/pm/recordatorios")
def pm_recordatorios(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    n = agente.recordatorios(db)
    flash(request, f"Se prepararon {n} recordatorios." if n else "No hay recordatorios nuevos que preparar.")
    return ir("/pm")


def _accion(db: Session, aid: int) -> AccionAgente:
    a = db.get(AccionAgente, aid)
    if not a:
        raise HTTPException(404)
    return a


@router.post("/acciones/{aid}/aprobar")
def accion_aprobar(aid: int, request: Request, ejecutar_ya: bool = Form(False), asunto: str = Form(None),
                   cuerpo: str = Form(None), db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    a = _accion(db, aid)
    if a.estado not in ("propuesta", "fallida"):
        raise HTTPException(400, "La acción ya fue resuelta.")
    if asunto is not None:
        a.asunto = asunto[:200]
    if cuerpo is not None:
        a.cuerpo = cuerpo
    acciones.aprobar(db, a, u)
    if ejecutar_ya:
        a.programada_para = utcnow()
        acciones.ejecutar(db, a)
    db.commit()
    flash(request, "Acción ejecutada." if a.estado == "ejecutada" else
          (f"La acción falló: {a.error}" if a.estado == "fallida" else "Acción aprobada; se ejecutará en su fecha."),
          "error" if a.estado == "fallida" else "ok")
    return ir(request.headers.get("referer") or "/pm")


@router.post("/acciones/{aid}/rechazar")
def accion_rechazar(aid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    a = _accion(db, aid)
    acciones.rechazar(db, a, u)
    db.commit()
    flash(request, "Acción descartada.")
    return ir(request.headers.get("referer") or "/pm")


@router.post("/acciones/ejecutar")
def acciones_ejecutar(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    n = acciones.ejecutar_pendientes(db)
    flash(request, f"Se ejecutaron {n} acciones programadas." if n else "No hay acciones aprobadas por ejecutar.")
    return ir("/pm")


@router.post("/acciones/nueva")
def accion_nueva(request: Request, tipo: str = Form(...), titulo: str = Form(...), destinatarios: list[int] = Form([]),
                 asunto: str = Form(""), cuerpo: str = Form(""), instrucciones: str = Form(""),
                 compromiso_id: OptInt = Form(None), db: Session = Depends(get_db),
                 u: Usuario = Depends(requiere_lider)):
    if tipo not in ("correo", "recordatorio", "informe"):
        raise HTTPException(400)
    from datetime import timedelta
    dup = db.scalar(select(AccionAgente).where(AccionAgente.creado_por == u.id, AccionAgente.tipo == tipo,
                                               AccionAgente.titulo == titulo[:200],
                                               AccionAgente.creado >= utcnow() - timedelta(minutes=2))
                    .order_by(AccionAgente.creado.desc()).limit(1))
    if dup:  # el mismo pedido llegó dos veces: se muestra el resultado del primero
        flash(request, "Ese pedido ya se estaba atendiendo; te mostramos el resultado.")
        return ir(f"/informes/{dup.informe_id}" if dup.informe_id else "/pm")
    a = acciones.crear(db, tipo=tipo, origen="usuario", titulo=titulo, destinatarios=destinatarios, asunto=asunto,
                       cuerpo=cuerpo, instrucciones=instrucciones or None,
                       entidad_tipo="compromiso" if compromiso_id else None, entidad_id=compromiso_id,
                       auto=True, creado_por=u.id)
    acciones.ejecutar(db, a)
    db.commit()
    flash(request, "Listo." if a.estado == "ejecutada" else f"No se pudo completar: {a.error}",
          "ok" if a.estado == "ejecutada" else "error")
    if a.informe_id:
        return ir(f"/informes/{a.informe_id}")
    return ir(request.headers.get("referer") or "/pm")


@router.get("/informes/{iid}")
def ver_informe(iid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    inf = db.get(Informe, iid)
    if not inf:
        raise HTTPException(404)
    if inf.tipo in ("pm_diario", "socio") and not u.es_lider:
        raise HTTPException(403)
    if inf.tipo not in ("pm_diario", "socio") and not u.es_lider:
        acc = db.scalar(select(AccionAgente).where(AccionAgente.informe_id == inf.id))
        if not acc or u.id not in (acc.destinatarios or []):
            raise HTTPException(403)
    return render(request, "informe.html", u, "pm", inf=inf)


@router.post("/informes/{iid}")
def editar_informe(iid: int, request: Request, contenido: str = Form(...), db: Session = Depends(get_db),
                   u: Usuario = Depends(requiere_lider)):
    inf = db.get(Informe, iid)
    if not inf:
        raise HTTPException(404)
    inf.contenido = contenido
    inf.origen = "editado"
    auditar(db, "editar", "informe", inf.id, u.id)
    db.commit()
    flash(request, "Informe actualizado.")
    return ir(f"/informes/{iid}")


# ---------------------------------------------------------------- estrategias (palancas)

@router.get("/estrategias")
def estrategias(request: Request, estado: str = "vigentes", db: Session = Depends(get_db),
                u: Usuario = Depends(usuario_actual)):
    q = select(Estrategia)
    if estado == "vigentes":
        q = q.where(Estrategia.estado.in_(("propuesta", "activa", "pausada")))
    elif estado in ESTADOS_ESTRATEGIA:
        q = q.where(Estrategia.estado == estado)
    lista = list(db.scalars(q.order_by(Estrategia.area_id, Estrategia.nombre)))
    stats = {e.id: seguimiento.stats_estrategia(db, e) for e in lista}
    return render(request, "estrategias.html", u, "estrategias", lista=lista, stats=stats, estado=estado,
                  areas=list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre))),
                  mcis=list(db.scalars(select(Mci).where(Mci.activo.is_(True)).order_by(Mci.nombre))),
                  usuarios=list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre))),
                  estados=ESTADOS_ESTRATEGIA)


@router.post("/estrategias")
def crear_estrategia(request: Request, area_id: int = Form(...), nombre: str = Form(...), descripcion: str = Form(""),
                     hipotesis: str = Form(""), mci_id: OptInt = Form(None), responsable_id: OptInt = Form(None),
                     seguimiento_: str = Form("semanal", alias="seguimiento"), activar: bool = Form(False),
                     db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    est = Estrategia(area_id=area_id, nombre=nombre.strip()[:200], descripcion=descripcion, hipotesis=hipotesis,
                     mci_id=mci_id or None, responsable_id=responsable_id or None, seguimiento=seguimiento_,
                     creado_por=u.id)
    db.add(est)
    db.flush()
    db.add(EstrategiaEvento(estrategia_id=est.id, tipo="creada", estado_nuevo="propuesta",
                            motivo="Alta en el catálogo de estrategias.", usuario_id=u.id))
    if activar:
        catalogo.cambiar_estado_estrategia(db, est, "activa", "Se activa al registrarla (acordada en junta).", u.id)
    auditar(db, "crear", "estrategia", est.id, u.id)
    db.commit()
    flash(request, "Estrategia registrada.")
    return ir(f"/estrategias/{est.id}")


@router.get("/estrategias/{eid}")
def ver_estrategia(eid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    est = db.get(Estrategia, eid)
    if not est:
        raise HTTPException(404)
    evals = list(db.scalars(select(RevisionIA).where(RevisionIA.tipo == "estrategia", RevisionIA.entidad_id == est.id)
                            .order_by(RevisionIA.creado.desc()).limit(5)))
    return render(request, "estrategia.html", u, "estrategias", est=est, st=seguimiento.stats_estrategia(db, est),
                  evals=evals, estados=ESTADOS_ESTRATEGIA,
                  usuarios=list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre))))


@router.post("/estrategias/{eid}/estado")
def estado_estrategia(eid: int, request: Request, estado: str = Form(...), motivo: str = Form(...),
                      evidencia: str = Form(""), db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    est = db.get(Estrategia, eid)
    if not est:
        raise HTTPException(404)
    try:
        catalogo.cambiar_estado_estrategia(db, est, estado, motivo, u.id, evidencia or None)
    except ValueError as e:
        flash(request, str(e), "error")
        return ir(f"/estrategias/{eid}")
    db.commit()
    flash(request, f"Estrategia ahora «{estado}». El cambio queda en su bitácora.")
    return ir(f"/estrategias/{eid}")


@router.post("/estrategias/{eid}/ajuste")
def ajuste_estrategia(eid: int, request: Request, descripcion: str = Form(...), motivo: str = Form(...),
                      responsable_id: OptInt = Form(None), db: Session = Depends(get_db),
                      u: Usuario = Depends(requiere_lider)):
    """Cambia cómo se ejecuta la estrategia sin perder la versión anterior: queda en el evento."""
    est = db.get(Estrategia, eid)
    if not est or not motivo.strip():
        raise HTTPException(400, "El ajuste requiere motivo.")
    antes = f"Descripción anterior: {est.descripcion}"
    est.descripcion = descripcion
    if responsable_id:
        est.responsable_id = responsable_id
    db.add(EstrategiaEvento(estrategia_id=est.id, tipo="ajuste", motivo=motivo, evidencia=antes, usuario_id=u.id))
    auditar(db, "ajuste", "estrategia", est.id, u.id, motivo=motivo)
    db.commit()
    flash(request, "Ajuste registrado.")
    return ir(f"/estrategias/{eid}")


@router.post("/estrategias/{eid}/nota")
def nota_estrategia(eid: int, request: Request, texto: str = Form(...), db: Session = Depends(get_db),
                    u: Usuario = Depends(usuario_actual)):
    est = db.get(Estrategia, eid)
    if not est:
        raise HTTPException(404)
    db.add(EstrategiaEvento(estrategia_id=est.id, tipo="nota", motivo=texto.strip()[:2000], usuario_id=u.id))
    db.commit()
    return ir(f"/estrategias/{eid}")


@router.post("/estrategias/{eid}/analizar")
def analizar(eid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    est = db.get(Estrategia, eid)
    if not est:
        raise HTTPException(404)
    try:
        rev = ia.analizar_estrategia(db, u, est, seguimiento.stats_estrategia(db, est))
    except (ia.IANoDisponible, ValueError):
        flash(request, "La IA no está disponible en este momento.", "error")
        return ir(f"/estrategias/{eid}")
    db.add(EstrategiaEvento(estrategia_id=est.id, tipo="evaluacion_ia",
                            motivo=f"IA: {rev.resultado['veredicto']} — {rev.resultado['diagnostico'][:400]}",
                            usuario_id=u.id))
    db.commit()
    flash(request, "La IA evaluó la estrategia con su histórico.")
    return ir(f"/estrategias/{eid}")


# ---------------------------------------------------------------- excepciones al cumplimiento

CATEGORIAS_EXCEPCION = {
    "fuerza_mayor": "Fuerza mayor / externa",
    "cliente": "Decisión o retraso del cliente",
    "autoridad": "Autoridad (aduana, SAT, VUCEM)",
    "cambio_prioridad": "Cambio de prioridad acordado por Dirección",
    "ausencia": "Ausencia justificada (vacaciones, incapacidad)",
    "meta_mal_calibrada": "Meta mal calibrada",
    "otra": "Otra",
}


@router.get("/excepciones")
def excepciones(request: Request, compromiso: int | None = None, db: Session = Depends(get_db),
                u: Usuario = Depends(requiere_lider)):
    lista = list(db.scalars(select(Excepcion).order_by(Excepcion.creado.desc()).limit(200)))
    if not u.es_direccion:
        lista = [e for e in lista if e.solicitada_por == u.id]
    etiquetas = {}
    for e in lista:
        etiquetas[e.id] = _etiqueta(db, e)
    comps = list(db.scalars(select(Compromiso).where(Compromiso.estado.in_(("abierto", "no_cumplido")))
                            .order_by(Compromiso.fecha_limite.desc()).limit(200)))
    if not u.es_direccion:
        comps = [c for c in comps if c.area_id == u.area_id]
    return render(request, "excepciones.html", u, "excepciones", lista=lista, etiquetas=etiquetas, comps=comps,
                  pre=f"compromiso:{compromiso}" if compromiso else "",
                  categorias=CATEGORIAS_EXCEPCION,
                  usuarios=list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre))),
                  preds=list(db.scalars(select(Predictiva).where(Predictiva.activo.is_(True)).order_by(Predictiva.nombre))),
                  mcis=list(db.scalars(select(Mci).where(Mci.activo.is_(True)).order_by(Mci.nombre))))


def _etiqueta(db: Session, e: Excepcion) -> str:
    modelo = {"compromiso": Compromiso, "persona": Usuario, "predictiva": Predictiva, "mci": Mci}[e.tipo_entidad]
    obj = db.get(modelo, e.entidad_id)
    if not obj:
        return f"{e.tipo_entidad} #{e.entidad_id}"
    texto = getattr(obj, "texto", None) or getattr(obj, "nombre", "")
    return f"{e.tipo_entidad.capitalize()}: {texto[:80]}"


@router.post("/excepciones")
def solicitar_excepcion(request: Request, objetivo: str = Form(...),
                        periodo_desde: date = Form(...), periodo_hasta: date = Form(...), categoria: str = Form(...),
                        efecto: str = Form("excluir"), meta_ajustada: OptFloat = Form(None),
                        justificacion: str = Form(...), visible_socio: bool = Form(False),
                        db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    tipo_entidad, _, ent = objetivo.partition(":")
    if tipo_entidad not in ("compromiso", "persona", "predictiva", "mci") or not ent.isdigit() \
            or categoria not in CATEGORIAS_EXCEPCION:
        raise HTTPException(400)
    if periodo_hasta < periodo_desde or not justificacion.strip():
        raise HTTPException(400, "Revisa el periodo y la justificación.")
    entidad_id = int(ent)
    e = Excepcion(tipo_entidad=tipo_entidad, entidad_id=entidad_id, periodo_desde=periodo_desde,
                  periodo_hasta=periodo_hasta, categoria=categoria,
                  efecto=efecto if efecto in ("excluir", "meta_ajustada") else "excluir",
                  meta_ajustada=meta_ajustada if efecto == "meta_ajustada" else None,
                  justificacion=justificacion.strip(), solicitada_por=u.id, visible_socio=visible_socio)
    db.add(e)
    db.flush()
    auditar(db, "solicitar", "excepcion", e.id, u.id, tipo=tipo_entidad, objetivo_id=entidad_id)
    db.commit()
    flash(request, "Excepción solicitada. Dirección la revisa; el dato original no se modifica.")
    return ir("/excepciones")


@router.post("/excepciones/{eid}/resolver")
def resolver_excepcion(eid: int, request: Request, decision: str = Form(...), comentario: str = Form(""),
                       visible_socio: bool = Form(False), db: Session = Depends(get_db),
                       u: Usuario = Depends(requiere_direccion)):
    e = db.get(Excepcion, eid)
    if not e or e.estado != "solicitada":
        raise HTTPException(400)
    e.estado = "aprobada" if decision == "aprobar" else "rechazada"
    e.resuelta_por, e.resuelta_en = u.id, utcnow()
    e.comentario_resolucion = comentario.strip() or None
    e.visible_socio = visible_socio
    auditar(db, e.estado, "excepcion", e.id, u.id, comentario=comentario)
    db.commit()
    flash(request, f"Excepción {e.estado}.")
    return ir("/excepciones")

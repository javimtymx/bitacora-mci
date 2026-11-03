"""Inicio, compromisos, remediaciones e ideas: la captura con revisión de IA."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Area,
    Compromiso,
    CompromisoNota,
    Estrategia,
    Idea,
    Informe,
    Predictiva,
    Remediacion,
    RevisionIA,
    Usuario,
    utcnow,
)
from ..services import acciones, catalogo, ia, parametros, seguimiento
from ..services.util import auditar, hoy, semana_iso, semanas_atras, viernes
from .base import check_password, flash, ir, puede_actuar, render, tocar_acceso, usuario_actual

router = APIRouter()


# ---------------------------------------------------------------- sesión

@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html", None)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    u = db.scalar(select(Usuario).where(Usuario.email == email.strip().lower()))
    if not u or not u.activo or not check_password(password, u.password_hash):
        flash(request, "Correo o contraseña incorrectos.", "error")
        return ir("/login")
    request.session.clear()
    request.session["uid"] = u.id
    tocar_acceso(db, u)
    db.commit()
    return ir("/")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return ir("/login")


# ---------------------------------------------------------------- helpers de captura

def _visibles(db: Session, u: Usuario, scope: str):
    q = select(Compromiso)
    if scope == "mios":
        q = q.where(Compromiso.responsable_id == u.id)
    elif scope == "area" or not u.es_direccion:
        q = q.where(Compromiso.area_id == u.area_id) if u.area_id else q.where(Compromiso.responsable_id == u.id)
    return q


def _opciones_captura(db: Session, u: Usuario) -> dict:
    areas = list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)))
    preds = catalogo.predictivas_por_area(db)
    if u.es_direccion:
        responsables = list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre)))
    elif u.rol == "lider":
        responsables = list(db.scalars(select(Usuario).where(Usuario.activo.is_(True), Usuario.area_id == u.area_id)
                                       .order_by(Usuario.nombre)))
    else:
        responsables = [u]
    estrategias = list(db.scalars(select(Estrategia).where(Estrategia.estado == "activa").order_by(Estrategia.nombre)))
    return {"areas": areas, "preds": preds, "responsables": responsables, "estrategias": estrategias,
            "viernes": viernes(), "sem": semana_iso(hoy())}


VEREDICTOS_FAVORABLES = ("ok", "fuerte", "media")


def _registrar_decision(db: Session, rev: RevisionIA | None, decision: str, motivo: str, entidad_id: int) -> None:
    if not rev:
        return
    rev.entidad_id = entidad_id
    if decision == "propio" and rev.veredicto not in VEREDICTOS_FAVORABLES:
        rev.aceptada = False
        rev.motivo_rechazo = motivo[:1000]
    else:
        rev.aceptada = True


def _revision_valida(db: Session, revision_id: int | None, tipo: str, u: Usuario) -> RevisionIA | None:
    if not revision_id:
        return None
    rev = db.get(RevisionIA, revision_id)
    if not rev or rev.tipo != tipo or rev.usuario_id != u.id or rev.aceptada is not None:
        raise HTTPException(400, "La revisión de la IA no es válida o ya se usó.")
    return rev


def _exigir_revision(db: Session, rev: RevisionIA | None, decision: str, motivo: str) -> None:
    if rev is None and parametros.get(db, "ia_revision_obligatoria") and ia.cliente().disponible:
        raise HTTPException(400, "Este registro debe pasar por la revisión de la IA antes de guardarse.")
    if rev is not None and decision == "propio" and rev.veredicto not in VEREDICTOS_FAVORABLES and not motivo.strip():
        raise HTTPException(400, "Explica por qué guardas sin aceptar la observación de la IA.")


def requiere_evidencia(db: Session, c: Compromiso) -> bool:
    return bool(c.predictiva and c.predictiva.requiere_evidencia and parametros.get(db, "evidencia_sesion_obligatoria"))


def tiene_evidencia(db: Session, c: Compromiso) -> bool:
    from ..models import SesionCliente
    return db.scalar(select(SesionCliente.id).where(SesionCliente.compromiso_id == c.id,
                                                    SesionCliente.evidencia_verificada.is_(True)).limit(1)) is not None


# ---------------------------------------------------------------- inicio

@router.get("/mi-semana")
def inicio(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    sem = semana_iso(hoy())
    q = _visibles(db, u, "area" if not u.es_direccion else "todos")
    recientes = list(db.scalars(q.order_by(Compromiso.actualizado.desc()).limit(10)))
    todos_sem = list(db.scalars(select(Compromiso).where(Compromiso.semana == sem)))
    al = seguimiento.alertas(db, None if u.es_direccion else u.area_id)
    personas = seguimiento.estado_personas(db, None if u.es_direccion else u.area_id)
    pm = db.scalar(select(Informe).where(Informe.tipo == "pm_diario").order_by(Informe.creado.desc()).limit(1))
    ideas_mes = len(list(db.scalars(select(Idea.id).where(
        Idea.creado >= utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)))))
    mis_alertas = [a for a in al if a.usuario and a.usuario.id == u.id]
    return render(request, "inicio.html", u, "mi-semana", recientes=recientes, seg=seguimiento,
                  n_semana=len(todos_sem), n_vencidos=sum(1 for c in todos_sem if seguimiento.vencido(c)),
                  n_sin_captura=sum(1 for p in personas if p.semana == 0), ideas_mes=ideas_mes, pm=pm,
                  mis_alertas=mis_alertas, **_opciones_captura(db, u))


# ---------------------------------------------------------------- compromisos

@router.get("/compromisos")
def compromisos(request: Request, scope: str = "mios", estado: str = "abiertos", rec: int | None = None,
                texto: str = "", db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    q = _visibles(db, u, scope)
    if estado == "abiertos":
        q = q.where(or_(Compromiso.estado == "abierto", Compromiso.estado == "no_cumplido"))
    else:
        q = q.where(Compromiso.semana.in_(semanas_atras(4)), Compromiso.estado.in_(("cumplido", "no_cumplido", "cancelado")))
    lista = list(db.scalars(q.order_by(Compromiso.fecha_limite)))
    if estado == "abiertos":
        lista = [c for c in lista if c.estado == "abierto" or (c.remediacion is None or c.remediacion.estado == "abierta")]
    from ..models import RecomendacionIA
    r = db.get(RecomendacionIA, rec) if rec else None
    return render(request, "compromisos.html", u, "compromisos", lista=lista, scope=scope, estado=estado,
                  seg=seguimiento, pref_texto=(r.texto if r else texto[:1000]), rec=r, **_opciones_captura(db, u))


@router.post("/compromisos")
def crear_compromiso(request: Request, area_id: int = Form(...), predictiva_id: int = Form(...),
                     responsable_id: int = Form(...), texto: str = Form(...), fecha_limite: date = Form(...),
                     estrategia_id: int | None = Form(None), revision_id: int | None = Form(None),
                     recomendacion_id: int | None = Form(None),
                     decision: str = Form("ok"), motivo: str = Form(""), db: Session = Depends(get_db),
                     u: Usuario = Depends(usuario_actual)):
    opciones = _opciones_captura(db, u)
    if responsable_id not in [r.id for r in opciones["responsables"]]:
        raise HTTPException(403, "No puedes registrar compromisos para esa persona.")
    pred = db.get(Predictiva, predictiva_id)
    if not pred:
        raise HTTPException(400, "Medida predictiva no válida.")
    rev = _revision_valida(db, revision_id, "compromiso", u)
    _exigir_revision(db, rev, decision, motivo)
    if rev and decision == "sugerencia" and rev.sugerencia:
        texto = rev.sugerencia
    c = Compromiso(semana=semana_iso(hoy()), area_id=area_id, responsable_id=responsable_id, autor_id=u.id,
                   predictiva_id=pred.id, predictiva_version_id=(catalogo.version_vigente(pred.versiones).id if pred.versiones else None),
                   estrategia_id=estrategia_id or None, texto=texto.strip()[:1000], fecha_limite=fecha_limite)
    from ..models import RecomendacionIA
    rec = db.get(RecomendacionIA, recomendacion_id) if recomendacion_id else None
    if rec:
        c.recomendacion_id = rec.id
        rec.estado = "convertida"
    db.add(c)
    db.flush()
    _registrar_decision(db, rev, decision, motivo, c.id)
    auditar(db, "crear", "compromiso", c.id, u.id, decision=decision)
    db.commit()
    flash(request, "Compromiso guardado.")
    return ir(f"/compromisos/{c.id}")


@router.get("/compromisos/{cid}")
def detalle_compromiso(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = db.get(Compromiso, cid)
    if not c or (not u.es_direccion and c.area_id != u.area_id and c.responsable_id != u.id):
        raise HTTPException(404)
    revs = list(db.scalars(select(RevisionIA).where(RevisionIA.entidad_id == c.id,
                                                     RevisionIA.tipo.in_(("compromiso", "remediacion")))
                           .order_by(RevisionIA.creado)))
    from ..models import AccionAgente
    acc = list(db.scalars(select(AccionAgente).where(AccionAgente.entidad_tipo == "compromiso",
                                                     AccionAgente.entidad_id == c.id).order_by(AccionAgente.creado)))
    from ..models import SesionCliente
    sesiones = list(db.scalars(select(SesionCliente).where(SesionCliente.compromiso_id == c.id)))
    return render(request, "compromiso.html", u, "compromisos", c=c, revs=revs, acciones=acc, sesiones=sesiones,
                  pide_evidencia=bool(c.predictiva and c.predictiva.requiere_evidencia),
                  evidencia_obligatoria=requiere_evidencia(db, c),
                  puede=puede_actuar(u, c), seg=seguimiento,
                  dias_max=parametros.get(db, "dias_max_remediacion"))


def _comp_editable(db: Session, cid: int, u: Usuario) -> Compromiso:
    c = db.get(Compromiso, cid)
    if not c:
        raise HTTPException(404)
    if not puede_actuar(u, c):
        raise HTTPException(403)
    return c


@router.post("/compromisos/{cid}/avance")
def avance(cid: int, request: Request, avance: int = Form(...), nota: str = Form(""), db: Session = Depends(get_db),
           u: Usuario = Depends(usuario_actual)):
    c = _comp_editable(db, cid, u)
    c.avance = max(0, min(100, avance))
    db.add(CompromisoNota(compromiso_id=c.id, usuario_id=u.id, avance=c.avance, texto=nota.strip()[:500]))
    db.commit()
    flash(request, "Avance guardado.")
    return ir(f"/compromisos/{cid}")


@router.post("/compromisos/{cid}/cumplido")
def cumplido(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = _comp_editable(db, cid, u)
    if requiere_evidencia(db, c) and not tiene_evidencia(db, c):
        flash(request, "Este compromiso necesita la transcripción de la sesión como evidencia. Cárgala en «Evidencia de la sesión».", "error")
        return ir(f"/compromisos/{cid}")
    c.estado, c.avance, c.cerrado = "cumplido", 100, utcnow()
    auditar(db, "cumplido", "compromiso", c.id, u.id)
    db.commit()
    flash(request, "Compromiso cumplido.")
    return ir(f"/compromisos/{cid}")


@router.post("/compromisos/{cid}/remediacion")
def remediar(cid: int, request: Request, causa: str = Form(...), accion: str = Form(...), nueva_fecha: date = Form(...),
             revision_id: int | None = Form(None), decision: str = Form("ok"), motivo: str = Form(""),
             plan_sel: list[int] = Form([]), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = _comp_editable(db, cid, u)
    dmax = parametros.get(db, "dias_max_remediacion")
    if nueva_fecha < hoy() or nueva_fecha > hoy() + timedelta(days=dmax):
        raise HTTPException(400, f"La nueva fecha debe estar entre hoy y {dmax} días.")
    rev = _revision_valida(db, revision_id, "remediacion", u)
    _exigir_revision(db, rev, decision, motivo)
    if rev and decision == "sugerencia" and rev.sugerencia:
        accion = rev.sugerencia
    cat = (rev.resultado.get("causa_categoria") if rev else None) or "otra"
    c.estado = "no_cumplido"
    c.cerrado = c.cerrado or utcnow()
    for r in c.remediaciones:
        if r.estado == "abierta":
            r.estado = "cancelada"
    r = Remediacion(compromiso_id=c.id, causa=causa.strip()[:1500], causa_categoria=cat, accion=accion.strip()[:1500],
                    nueva_fecha=nueva_fecha, creado_por=u.id)
    db.add(r)
    db.flush()
    _registrar_decision(db, rev, decision, motivo, c.id)
    creadas = []
    if rev and rev.resultado.get("plan"):
        creadas = acciones.plan_desde_revision(db, c, rev.resultado["plan"], u,
                                               auto=parametros.get(db, "auto_plan_remediacion"), seleccion=plan_sel)
    auditar(db, "remediacion", "compromiso", c.id, u.id, decision=decision, acciones=len(creadas))
    db.commit()
    flash(request, f"Remediación registrada{f' con {len(creadas)} actividades de seguimiento' if creadas else ''}.")
    return ir(f"/compromisos/{cid}")


@router.post("/remediaciones/{rid}/cerrar")
def cerrar_remediacion(rid: int, request: Request, resultado: str = Form(...), comentario: str = Form(""),
                       db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    r = db.get(Remediacion, rid)
    if not r:
        raise HTTPException(404)
    _comp_editable(db, r.compromiso_id, u)
    r.estado = "cumplida" if resultado == "cumplida" else "no_cumplida"
    r.resultado = comentario.strip()[:1000]
    r.cerrada = utcnow()
    auditar(db, "cerrar_remediacion", "remediacion", r.id, u.id, resultado=r.estado)
    db.commit()
    flash(request, "Remediación actualizada.")
    return ir(f"/compromisos/{r.compromiso_id}")


# ---------------------------------------------------------------- ideas

@router.get("/ideas")
def ideas(request: Request, filtro: str = "activas", db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    q = select(Idea)
    if filtro == "mias":
        q = q.where(Idea.autor_id == u.id)
    elif filtro == "activas":
        q = q.where(Idea.estado.not_in(("implementada", "descartada")))
    lista = list(db.scalars(q.order_by(Idea.actualizado.desc()).limit(300)))
    mes = utcnow().replace(day=1, hour=0, minute=0, second=0)
    del_mes = list(db.scalars(select(Idea).where(Idea.creado >= mes)))
    matriz = {k: sum(1 for i in del_mes if i.cuadrante == k) for k in ("Ganancia rápida", "Proyecto", "Relleno", "Reconsiderar")}
    implementadas = sum(1 for i in db.scalars(select(Idea).where(Idea.estado == "implementada", Idea.actualizado >= mes)))
    areas = list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)))
    return render(request, "ideas.html", u, "ideas", lista=lista, filtro=filtro, matriz=matriz,
                  implementadas=implementadas, areas=areas)


@router.post("/ideas")
def crear_idea(request: Request, titulo: str = Form(...), problema: str = Form(...), beneficio: str = Form(""),
               origen: str = Form(...), area_id: int = Form(...), revision_id: int | None = Form(None),
               decision: str = Form("ok"), motivo: str = Form(""), db: Session = Depends(get_db),
               u: Usuario = Depends(usuario_actual)):
    rev = _revision_valida(db, revision_id, "idea", u)
    if rev is None and parametros.get(db, "ia_revision_obligatoria") and ia.cliente().disponible:
        raise HTTPException(400, "La idea debe pasar por la evaluación de la IA antes de registrarse.")
    if rev and rev.veredicto == "debil" and decision == "propio" and not motivo.strip():
        raise HTTPException(400, "Explica por qué registras la idea sin atender las observaciones.")
    res = rev.resultado if rev else {}
    i = Idea(autor_id=u.id, area_id=area_id, titulo=titulo.strip()[:200], problema=problema.strip()[:3000],
             beneficio=beneficio.strip()[:3000], origen=origen[:40], impacto=res.get("impacto"),
             esfuerzo=res.get("esfuerzo"), cuadrante=res.get("cuadrante"), fortaleza=res.get("fortaleza"),
             duplicado_de=res.get("duplicado_de"))
    db.add(i)
    db.flush()
    _registrar_decision(db, rev, decision, motivo, i.id)
    auditar(db, "crear", "idea", i.id, u.id)
    db.commit()
    flash(request, "Idea registrada.")
    return ir("/ideas")


@router.post("/ideas/{iid}/avance")
def avance_idea(iid: int, request: Request, avance: int = Form(...), db: Session = Depends(get_db),
                u: Usuario = Depends(usuario_actual)):
    i = db.get(Idea, iid)
    if not i or (i.autor_id != u.id and not u.es_lider):
        raise HTTPException(403)
    i.avance = max(0, min(100, avance))
    i.estado = "implementada" if i.avance >= 100 else "en_curso"
    db.commit()
    return ir("/ideas")


@router.post("/ideas/{iid}/estado")
def estado_idea(iid: int, estado: str = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    i = db.get(Idea, iid)
    if not i or not u.es_lider:
        raise HTTPException(403)
    from ..models import ESTADOS_IDEA
    if estado in ESTADOS_IDEA:
        i.estado = estado
        auditar(db, "estado", "idea", i.id, u.id, estado=estado)
        db.commit()
    return ir("/ideas")


# ---------------------------------------------------------------- API de revisión IA (JSON)

def _ia_error(e: Exception) -> JSONResponse:
    sin_llave = not ia.cliente().disponible
    return JSONResponse({"ok": False, "sin_ia": sin_llave,
                         "mensaje": "La IA no está configurada. Puedes guardar sin revisión." if sin_llave
                         else "La IA no respondió. Intenta de nuevo en un momento."})


@router.post("/api/ia/compromiso")
def api_rev_compromiso(area_id: int = Form(...), predictiva_id: int = Form(...), responsable_id: int = Form(...),
                       texto: str = Form(...), fecha_limite: date = Form(...), estrategia_id: int | None = Form(None),
                       db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    pred = db.get(Predictiva, predictiva_id)
    resp = db.get(Usuario, responsable_id)
    area = db.get(Area, area_id)
    est = db.get(Estrategia, estrategia_id) if estrategia_id else None
    if not (pred and resp and area):
        return JSONResponse({"ok": False, "mensaje": "Completa el área, la medida y el responsable."})
    try:
        rev = ia.revisar_compromiso(db, u, area=area.nombre, predictiva=pred.nombre, texto=texto,
                                    fecha_limite=fecha_limite, responsable=resp, estrategia=est.nombre if est else "")
    except (ia.IANoDisponible, ValueError) as e:
        return _ia_error(e)
    db.commit()
    return {"ok": True, "revision_id": rev.id, **rev.resultado}


@router.post("/api/ia/remediacion/{cid}")
def api_rev_remediacion(cid: int, causa: str = Form(...), accion: str = Form(...), nueva_fecha: date = Form(...),
                        db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = _comp_editable(db, cid, u)
    try:
        rev = ia.revisar_remediacion(db, u, c, causa=causa, accion=accion, nueva_fecha=nueva_fecha,
                                     dias_max=parametros.get(db, "dias_max_remediacion"))
    except (ia.IANoDisponible, ValueError) as e:
        return _ia_error(e)
    db.commit()
    return {"ok": True, "revision_id": rev.id, **rev.resultado}


@router.post("/api/ia/idea")
def api_rev_idea(titulo: str = Form(...), problema: str = Form(...), beneficio: str = Form(""), origen: str = Form(...),
                 area_id: int = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    area = db.get(Area, area_id)
    try:
        rev = ia.revisar_idea(db, u, titulo=titulo, problema=problema, beneficio=beneficio, origen=origen,
                              area=area.nombre if area else "")
    except (ia.IANoDisponible, ValueError) as e:
        return _ia_error(e)
    db.commit()
    dup = db.get(Idea, rev.resultado.get("duplicado_de")) if rev.resultado.get("duplicado_de") else None
    return {"ok": True, "revision_id": rev.id, **rev.resultado, "duplicado_titulo": dup.titulo if dup else ""}

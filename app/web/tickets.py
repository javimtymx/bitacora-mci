"""GuardIA Ticket: captura híbrida, triage, tablero de desarrollo y configuración."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import (TICKET_CIERRES, TICKET_ESTADOS, TICKET_IMPACTOS, TICKET_TIPOS, Ticket, TicketAnexo,
                      TicketDiagnostico, TicketPrompt, TicketRegla, Usuario, utcnow)
from ..services import accesos, almacen, correo, ia
from ..services import subidas as subidas_srv
from ..services import tickets as tk
from .base import OptDate, OptInt, flash, ir, render, usuario_actual

router = APIRouter()
log = logging.getLogger("bitacora.web.tickets")


def _ticket(db: Session, tid: int, u: Usuario) -> Ticket:
    t = db.get(Ticket, tid)
    if not t or not tk.puede_ver(u, t):
        raise HTTPException(404, "Ticket no encontrado.")
    return t


def _exigir(u: Usuario, clave: str) -> None:
    if not accesos.puede(u, clave):
        raise HTTPException(403, "No tienes permiso para esta acción de tickets.")


def _avisar(bg: BackgroundTasks, evento: str, t: Ticket, u: Usuario | None, **x) -> None:
    """Correos del ticket en segundo plano (ver services/tickets_correo.py)."""
    from ..services import tickets_correo
    bg.add_task(tickets_correo.en_fondo, evento, t.id, u.id if u else None, **x)


async def _archivos(lista: list[UploadFile] | None) -> list[tuple[str, bytes]]:
    out = []
    for a in lista or []:
        if a and a.filename:
            out.append((a.filename, await a.read()))
    return out


# ---------------------------------------------------------------- usuario: mis tickets y alta

PESTANAS = {
    "pendientes": ("Pendientes", ("nuevo", "pendiente", "info")),
    "programados": ("Programados", ("planeado", "en_curso")),
    "cerrar": ("Por cerrar y cerrados", ("revision", "cerrado")),
    "todos": ("Todos", None),
}


@router.get("/tickets")
def lista(request: Request, tab: str = "pendientes", ver: str = "mios", q: str = "", db: Session = Depends(get_db),
          u: Usuario = Depends(usuario_actual)):
    tab = tab if tab in PESTANAS else "pendientes"
    ver = ver if ver in ("mios", "equipo", "empresa") else "mios"
    equipo = db.scalars(select(Usuario.id).where(Usuario.jefe_id == u.id)).all()
    base = select(Ticket)
    if ver == "mios":
        base = base.where(or_(Ticket.creado_por == u.id, Ticket.asignado_id == u.id))
    elif ver == "equipo":
        base = base.where(Ticket.creado_por.in_([u.id, *equipo]))
    q = q.strip()[:120]
    if q:
        base = base.where(tk.filtro_busqueda(q))
    conteo = {}
    for k, (_, estados) in PESTANAS.items():
        cq = base.with_only_columns(func.count(Ticket.id))
        if estados:
            cq = cq.where(Ticket.estado.in_(estados))
        conteo[k] = db.scalar(cq) or 0
    estados = PESTANAS[tab][1]
    lq = base.where(Ticket.estado.in_(estados)) if estados else base
    if tab == "programados":
        lq = lq.order_by(Ticket.fecha_entrega.is_(None), Ticket.fecha_entrega, Ticket.id)
    elif tab == "cerrar":
        lq = lq.order_by((Ticket.estado == "cerrado"), Ticket.cerrado_en.desc(), Ticket.id.desc())
    else:
        lq = lq.order_by(Ticket.creado.desc())
    ts = db.scalars(lq.limit(400)).all()
    return render(request, "tickets.html", u, "tickets", ts=ts, ver=ver, tab=tab, q=q, pestanas=PESTANAS,
                  conteo=conteo, tiene_equipo=bool(equipo), staff=tk.es_staff(u), hoy=date.today(),
                  estados=TICKET_ESTADOS, tipos=tk.tipos(db, True), vence_en=tk.vence_en)


@router.get("/tickets/nuevo")
def nuevo(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = tk.prompt_vigente(db)
    cat = tk.catalogo(db)
    db.commit()
    tipos_act = tk.tipos(db)
    req = {t: tk.requeridos(p, t) for t in tipos_act}
    return render(request, "ticket_nuevo.html", u, "tickets", catalogo=cat, requeridos=req, campos_req=tk.CAMPOS_REQ,
                  tipos=tipos_act, impactos=tk.impactos(db), prompt_version=p.version,
                  ia_ok=ia.cliente().disponible, aceptar=",".join("." + e for e in tk.PERMITIDOS))


@router.post("/api/tickets/revisar")
async def api_revisar(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    """GuardIA revisa el formulario (y responde en el chat). Si no responde, la pantalla pasa a modo manual."""
    try:
        datos = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"ok": False, "mensaje": "Solicitud inválida."}, status_code=400)
    campos = datos.get("campos") or {}
    anexos = [str(x)[:200] for x in (datos.get("anexos") or [])][:20]
    historial = [h for h in (datos.get("historial") or []) if isinstance(h, dict)][-20:]
    mensaje = str(datos.get("mensaje") or "").strip()[:2000] or None
    try:
        r = tk.revisar(db, campos, anexos, historial, mensaje, solo_llenar=bool(datos.get("solo_llenar")),
                       omitidos=[str(x) for x in (datos.get("omitidos") or [])][:10])
    except ia.IANoDisponible as e:
        return JSONResponse({"ok": False, "sin_ia": True, "mensaje": str(e)})
    except Exception:  # noqa: BLE001 - cualquier falla de GuardIA lleva al modo manual, nunca bloquea
        log.exception("Revisión de ticket falló")
        return JSONResponse({"ok": False, "sin_ia": True, "mensaje": "GuardIA no respondió."})
    db.commit()  # el prompt inicial y el catálogo pudieron crearse
    return JSONResponse({"ok": True, **r})


@router.post("/api/tickets/nuevo")
async def api_nuevo(request: Request, bg: BackgroundTasks, descripcion: str = Form(""), producto: str = Form(""),
                    menu: str = Form(""), pantalla: str = Form(""), tipo: str = Form(""), impacto: str = Form(""),
                    titulo: str = Form(""), pasos: str = Form(""), esperado: str = Form(""), obtenido: str = Form(""),
                    referencia: str = Form(""), cliente: str = Form(""), validacion: str = Form("sin_ia"),
                    faltantes: str = Form("[]"), conversacion: str = Form("[]"), prompt_version: OptInt = Form(None),
                    archivos: list[UploadFile] | None = File(None), subidas: list[str] = Form([]), cc: str = Form(""),
                    db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    campos = dict(descripcion=descripcion, producto=producto, menu=menu, pantalla=pantalla, tipo=tipo, impacto=impacto,
                  titulo=titulo, pasos=pasos, esperado=esperado, obtenido=obtenido, referencia=referencia, cliente=cliente)
    try:
        falt = [str(x) for x in json.loads(faltantes or "[]")][:10]
        conv = [h for h in json.loads(conversacion or "[]") if isinstance(h, dict)][-30:]
    except (json.JSONDecodeError, TypeError):
        falt, conv = [], []
    try:
        subidos = [subidas_srv.recibir(x, u.id, "ticket") for x in subidas]
        t = tk.crear(db, u, campos, await _archivos(archivos), validacion=validacion, faltantes=falt,
                     conversacion=[{"de": str(h.get("de"))[:2], "t": str(h.get("t", ""))[:2000]} for h in conv],
                     prompt_version=prompt_version, subidos=subidos, cc=tk.limpiar_cc(cc))
        db.commit()
    except (tk.TicketInvalido, subidas_srv.SubidaInvalida) as e:
        db.rollback()
        return JSONResponse({"ok": False, "mensaje": str(e)}, status_code=400)
    bg.add_task(tk.triage_en_fondo, t.id)
    _avisar(bg, "nuevo", t, u)
    flash(request, f"Ticket {t.folio} levantado. Te avisaremos por correo cuando haya novedades.")
    return JSONResponse({"ok": True, "url": f"/tickets/{t.id}", "folio": t.folio})


@router.get("/tickets/{tid}")
def detalle(tid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    staff = tk.es_staff(u)
    sim = tk.similares(db, f"{t.titulo} {t.descripcion} {t.pantalla}", excluir_id=t.id) if staff else []
    return render(request, "ticket.html", u, "tickets", t=t, staff=staff, sim=sim,
                  triage=accesos.puede(u, "tickets-triage"), dev=accesos.puede(u, "tickets-tablero"),
                  estados=TICKET_ESTADOS, tipos=tk.tipos(db, True), impactos=tk.impactos(db, True), cierres=TICKET_CIERRES,
                  campos_req=tk.CAMPOS_REQ, asignables=tk.asignables(db) if staff else [], vence_en=tk.vence_en,
                  aceptar=",".join("." + e for e in tk.PERMITIDOS), ve_costos=tk.ve_costos(u),
                  edita_cc=tk.puede_editar_cc(u, t), hoy=date.today(), areas_u=tk.areas_usuario(db, True),
                  gente=tk.asignables(db) if accesos.puede(u, "tickets-tablero") else [])


@router.post("/tickets/{tid}/comentar")
async def comentar(tid: int, request: Request, bg: BackgroundTasks, texto: str = Form(""),
                   archivos: list[UploadFile] | None = File(None), subidas: list[str] = Form([]),
                   db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    era_info = t.estado == "info"
    lista_: list = []
    try:
        lista_ = await _archivos(archivos)
        for nombre, contenido in lista_:
            tk.guardar_anexo(db, t, nombre, contenido, u)
        for x in subidas:
            a = tk.registrar_subida(db, t, subidas_srv.recibir(x, u.id, "ticket"), u)
            lista_.append((a.nombre, b""))
        if texto.strip() or not lista_:
            tk.comentar(db, u, t, texto or "")
        elif era_info and u.id == t.creado_por:
            tk.comentar(db, u, t, "Adjunté " + ", ".join(n for n, _ in lista_) + ".")
        db.commit()
    except (tk.TicketInvalido, subidas_srv.SubidaInvalida) as e:
        db.rollback()
        flash(request, str(e), "error")
        return ir(f"/tickets/{tid}")
    texto_aviso = texto.strip() or ("Adjuntó: " + ", ".join(n for n, _ in lista_) if lista_ else "")
    if era_info and t.estado != "info":
        _avisar(bg, "respuesta", t, u, texto=texto_aviso)
    else:
        _avisar(bg, "comentario", t, u, texto=texto_aviso)
    return ir(f"/tickets/{tid}#hilo")


@router.get("/anexos-ticket/{aid}")
def descargar(aid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = db.get(TicketAnexo, aid)
    if not a or not tk.puede_ver(u, a.ticket):
        raise HTTPException(404, "Archivo no encontrado.")
    if (a.bytes or 0) > 20 * 1024 * 1024 and (url := almacen.url_descarga(a.ruta, a.nombre, a.tipo)):
        return RedirectResponse(url, status_code=302)  # los grandes bajan directo de S3
    try:
        contenido = almacen.leer(a.ruta)
    except almacen.AlmacenError as e:
        raise HTTPException(404, str(e)) from e
    return Response(contenido, media_type=a.tipo or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(a.nombre)}",
                             "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- desarrollo

@router.post("/tickets/{tid}/mover")
def mover(tid: int, request: Request, bg: BackgroundTasks, estado: str = Form(...), db: Session = Depends(get_db),
          u: Usuario = Depends(usuario_actual)):
    """Cambio de etapa (botones o arrastrar en el Kanban). Responde JSON cuando lo pide el Kanban."""
    _exigir(u, "tickets-tablero")
    t = _ticket(db, tid, u)
    quiere_json = "application/json" in (request.headers.get("accept") or "")
    try:
        antes = t.estado
        tk.mover(db, u, t, estado)
        db.commit()
        if t.estado != antes:
            _avisar(bg, "etapa", t, u)
    except tk.TicketInvalido as e:
        if quiere_json:
            return JSONResponse({"ok": False, "mensaje": str(e), "planear": "planéalo" in str(e)}, status_code=400)
        flash(request, str(e), "error")
    if quiere_json:
        return JSONResponse({"ok": True, "estado": t.estado})
    destino = request.headers.get("referer") or f"/tickets/{tid}"
    return ir(destino if "/tickets" in destino else f"/tickets/{tid}")


@router.post("/tickets/{tid}/planear")
def planear(tid: int, request: Request, bg: BackgroundTasks, horas: str = Form(""), fecha_entrega: OptDate = Form(None),
            asignado_id: OptInt = Form(None), volver: str = Form(""), motivo: str = Form(""), db: Session = Depends(get_db),
            u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    t = _ticket(db, tid, u)
    asignado = db.get(Usuario, asignado_id) if asignado_id else None
    if asignado and asignado not in tk.asignables(db):
        raise HTTPException(400, "Esa persona no atiende tickets.")
    antes = t.estado
    try:
        tk.planear(db, u, t, horas=horas, fecha_entrega=fecha_entrega, asignado=asignado, motivo=motivo)
        db.commit()
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
        return ir(f"/tickets/{tid}#planear")
    _avisar(bg, "etapa", t, u)
    flash(request, f"{t.folio} {'planeado' if antes != t.estado else 'actualizado'}: entrega {t.fecha_entrega:%d/%m/%Y}.")
    return ir("/tickets-tablero" if volver == "tablero" else f"/tickets/{tid}")


@router.post("/tickets/{tid}/cc")
def editar_cc(tid: int, request: Request, cc: str = Form(""), db: Session = Depends(get_db),
              u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    if not tk.puede_editar_cc(u, t):
        raise HTTPException(403, "Solo quien levantó el ticket, su jefe o soporte cambian las copias.")
    try:
        t.cc = tk.limpiar_cc(cc)
        db.commit()
        flash(request, "Copias actualizadas.")
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
    return ir(f"/tickets/{tid}")


@router.post("/tickets/{tid}/pedir-info")
def pedir_info(tid: int, request: Request, bg: BackgroundTasks, pregunta: str = Form(""), regla: str = Form(""),
               db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    t = _ticket(db, tid, u)
    try:
        r = tk.pedir_info(db, u, t, pregunta, regla)
        db.commit()
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
        return ir(f"/tickets/{tid}")
    _avisar(bg, "pedir_info", t, u, pregunta=pregunta.strip())
    flash(request, "Se pidió la información al usuario." + (" Tu regla quedó para revisión en Configurar GuardIA Ticket." if r else ""))
    return ir(f"/tickets/{tid}")


@router.post("/tickets/{tid}/paquete")
def paquete(tid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    t = _ticket(db, tid, u)
    tk.generar_paquete(db, t)
    db.commit()
    return ir(f"/tickets/{tid}#paquete")


@router.post("/tickets/{tid}/cerrar")
def cerrar(tid: int, request: Request, bg: BackgroundTasks, tipo_cierre: str = Form(""), causa: str = Form(""),
           solucion: str = Form(""), modulos: str = Form(""), horas_reales: str = Form(""), db: Session = Depends(get_db),
           u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    t = _ticket(db, tid, u)
    try:
        tk.cerrar(db, u, t, tipo_cierre=tipo_cierre, causa=causa, solucion=solucion, modulos=modulos,
                  horas_reales=horas_reales or None)
        db.commit()
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
        return ir(f"/tickets/{tid}")
    _avisar(bg, "cerrado", t, u)
    flash(request, f"{t.folio} cerrado. La solución queda disponible para tickets parecidos.")
    return ir(f"/tickets/{tid}")


@router.post("/tickets/{tid}/reabrir")
def reabrir(tid: int, request: Request, bg: BackgroundTasks, motivo: str = Form(""), db: Session = Depends(get_db),
            u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    if not (u.id == t.creado_por or accesos.puede(u, "tickets-tablero")):
        raise HTTPException(403, "No puedes reabrir este ticket.")
    try:
        tk.reabrir(db, u, t, motivo)
        db.commit()
        _avisar(bg, "reabierto", t, u, motivo=motivo.strip())
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
    return ir(f"/tickets/{tid}")


@router.get("/tickets-tablero")
def tablero(request: Request, persona: OptInt = None, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    q = select(Ticket).where(or_(Ticket.estado.in_(tk.ABIERTOS_ATENCION),
                                 Ticket.cerrado_en >= utcnow() - timedelta(days=7)))
    if persona:
        q = q.where(Ticket.asignado_id == persona)
    orden = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    ts = sorted(db.scalars(q).all(), key=lambda t: (orden.get(t.prioridad or "", 4), t.sla_vence or t.creado))
    columnas = [(e, TICKET_ESTADOS[e], [t for t in ts if t.estado == e])
                for e in ("pendiente", "planeado", "en_curso", "revision", "info", "cerrado")]
    n_nuevos = len(db.scalars(select(Ticket.id).where(Ticket.estado == "nuevo")).all())
    return render(request, "tickets_tablero.html", u, "tickets-tablero", columnas=columnas, persona=persona,
                  gente=tk.asignables(db), vence_en=tk.vence_en, n_nuevos=n_nuevos, hoy=date.today(), tipos=tk.tipos(db, True))


# ---------------------------------------------------------------- triage

@router.get("/tickets-triage")
def triage_lista(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    nuevos = db.scalars(select(Ticket).where(Ticket.estado == "nuevo").order_by(Ticket.creado)).all()
    recientes = db.scalars(select(Ticket).where(Ticket.triage_en.is_not(None)).order_by(Ticket.triage_en.desc()).limit(300)).all()
    gente = tk.asignables(db)
    hace = utcnow() - timedelta(minutes=3)
    esperando = {t.id for t in nuevos if not t.ia_triage and (t.creado >= hace or t.actualizado >= hace)}
    return render(request, "tickets_triage.html", u, "tickets-triage", nuevos=nuevos, recientes=recientes, gente=gente,
                  esperando=esperando,
                  nombres={g.id: g.nombre for g in gente}, tipos=tk.tipos(db, True), impactos=tk.impactos(db, True),
                  campos_req=tk.CAMPOS_REQ, estados=TICKET_ESTADOS)


@router.post("/tickets-triage/{tid}")
def triage_guardar(tid: int, request: Request, bg: BackgroundTasks, prioridad: str = Form(""),
                   asignado_id: OptInt = Form(None), nota: str = Form(""), db: Session = Depends(get_db),
                   u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    asignado = db.get(Usuario, asignado_id) if asignado_id else None
    if asignado and asignado not in tk.asignables(db):
        raise HTTPException(400, "Esa persona no atiende tickets.")
    try:
        tk.triage(db, u, t, prioridad, asignado, nota)
        db.commit()
    except tk.TicketInvalido as e:
        flash(request, str(e), "error")
        return ir("/tickets-triage")
    _avisar(bg, "triage", t, u)
    flash(request, f"{t.folio}: {t.prioridad}" + (f" para {asignado.nombre}" if asignado else "") + ".")
    return ir("/tickets-triage")


@router.post("/tickets-triage/{tid}/ia")
def triage_regenerar(tid: int, request: Request, bg: BackgroundTasks, db: Session = Depends(get_db),
                     u: Usuario = Depends(usuario_actual)):
    t = _ticket(db, tid, u)
    t.ia_triage, t.actualizado = {}, utcnow()
    db.commit()
    bg.add_task(tk.triage_en_fondo, t.id)
    return ir("/tickets-triage")


# ---------------------------------------------------------------- configuración

@router.get("/tickets-config")
def config(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = tk.prompt_vigente(db)
    tk.catalogo(db)
    tk.tipos(db), tk.impactos(db)  # en una instalación nueva crea las opciones iniciales antes de guardar
    db.commit()
    versiones = db.scalars(select(TicketPrompt).order_by(TicketPrompt.version.desc()).limit(20)).all()
    reglas = db.scalars(select(TicketRegla).where(TicketRegla.estado == "pendiente").order_by(TicketRegla.creado)).all()
    return render(request, "tickets_config.html", u, "tickets-config", p=p, versiones=versiones, reglas=reglas,
                  tipos=tk.tipos(db), campos_req=tk.CAMPOS_REQ, catalogo_txt=tk.catalogo_texto(db),
                  op_tipo=tk.opciones(db, "tipo", True), op_impacto=tk.opciones(db, "impacto", True),
                  uso_tipo=tk.en_uso(db, "tipo"), uso_impacto=tk.en_uso(db, "impacto"),
                  metricas=tk.regresados_por_version(db))


@router.post("/tickets-config/prompt")
async def config_prompt(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    form = await request.form()
    req = {t: [c for c in tk.CAMPOS_REQ if form.get(f"req_{t}_{c}")] for t in tk.tipos(db)}
    try:
        p = tk.nueva_version(db, u, instrucciones=str(form.get("instrucciones") or ""),
                             reglas=str(form.get("reglas") or "").splitlines(), requeridos_=req,
                             motivo=str(form.get("motivo") or ""))
        db.commit()
        flash(request, f"Versión {p.version} activa: GuardIA ya revisa los tickets nuevos con estas instrucciones.")
    except tk.TicketInvalido as e:
        db.rollback()
        flash(request, str(e), "error")
    return ir("/tickets-config")


@router.post("/tickets-config/opciones/{catalogo}")
async def config_opciones(catalogo: str, request: Request, db: Session = Depends(get_db),
                          u: Usuario = Depends(usuario_actual)):
    form = await request.form()
    filas = []
    for i in range(60):
        if f"etiqueta_{i}" not in form:
            continue
        oid = str(form.get(f"id_{i}") or "")
        filas.append({"id": int(oid) if oid.isdigit() else None, "etiqueta": str(form.get(f"etiqueta_{i}") or ""),
                      "orden": str(form.get(f"orden_{i}") or "0").strip() or "0", "activo": bool(form.get(f"activo_{i}")),
                      "prioridad": str(form.get(f"prioridad_{i}") or ""), "menor": bool(form.get(f"menor_{i}"))})
    for f in filas:
        f["orden"] = int(f["orden"]) if str(f["orden"]).lstrip("-").isdigit() else 0
    try:
        n = tk.guardar_opciones(db, u, catalogo, filas)
        db.commit()
        flash(request, f"Catálogo de {'tipos' if catalogo == 'tipo' else 'impactos'} guardado ({n} opción(es)).")
    except tk.TicketInvalido as e:
        db.rollback()
        flash(request, str(e), "error")
    return ir("/tickets-config#opciones")


@router.post("/tickets-config/catalogo")
def config_catalogo(request: Request, catalogo: str = Form(""), db: Session = Depends(get_db),
                    u: Usuario = Depends(usuario_actual)):
    n = tk.guardar_catalogo(db, u, catalogo)
    db.commit()
    flash(request, f"Catálogo guardado: {n} líneas.")
    return ir("/tickets-config#catalogo")


@router.post("/tickets-config/reglas/{rid}")
def config_regla(rid: int, request: Request, accion: str = Form(...), texto: str = Form(""),
                 db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    r = db.get(TicketRegla, rid)
    if not r or r.estado != "pendiente":
        raise HTTPException(404, "Solicitud no encontrada.")
    if accion == "aprobar":
        p = tk.aprobar_regla(db, u, r, texto)
        flash(request, f"Regla agregada: versión {p.version} del prompt activa.")
    else:
        r.estado, r.resuelto_por, r.resuelto_en = "descartada", u.id, utcnow()
        flash(request, "Solicitud descartada.")
    db.commit()
    return ir("/tickets-config")


@router.post("/tickets-config/restaurar/{version}")
def config_restaurar(version: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    v = db.scalar(select(TicketPrompt).where(TicketPrompt.version == version))
    if not v:
        raise HTTPException(404, "Versión no encontrada.")
    p = tk.nueva_version(db, u, instrucciones=v.instrucciones, reglas=list(v.reglas or []),
                         requeridos_=v.requeridos or tk.REQUERIDOS_DEFAULT, motivo=f"Se restauró la versión {version}")
    db.commit()
    flash(request, f"Se restauró la versión {version} como versión {p.version}.")
    return ir("/tickets-config")


# ---------------------------------------------------------------- analítica y diagnóstico

@router.get("/tickets-analitica")
def analitica(request: Request, dias: int = 30, por: str = "area", db: Session = Depends(get_db),
              u: Usuario = Depends(usuario_actual)):
    from ..services import tickets_analitica as ta
    dias = dias if dias in (7, 30, 90, 180) else 30
    por = por if por in ta.DIMENSIONES else "area"
    m = ta.metricas(db, dias, por)
    diag = db.scalar(select(TicketDiagnostico).where(TicketDiagnostico.dimension == por)
                     .order_by(TicketDiagnostico.creado.desc()).limit(1))
    generando = bool(diag and diag.estado == "generando" and diag.creado >= utcnow() - timedelta(minutes=5))
    maximo = max([s["levantados"] for s in m["semanas"]] + [s["cerrados"] for s in m["semanas"]] + [1])
    return render(request, "tickets_analitica.html", u, "tickets-analitica", m=m, diag=diag, generando=generando,
                  dimensiones=ta.DIMENSIONES, maximo=maximo)


@router.post("/tickets-analitica/diagnostico")
def analitica_diagnostico(request: Request, bg: BackgroundTasks, por: str = Form("area"), dias: int = Form(30),
                          db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    from ..services import tickets_analitica as ta
    por = por if por in ta.DIMENSIONES else "area"
    dias = dias if dias in (7, 30, 90, 180) else 30
    d = TicketDiagnostico(dimension=por, dias=dias, generado_por=u.id)
    db.add(d)
    db.commit()
    bg.add_task(ta.generar_en_fondo, d.id)
    return ir(f"/tickets-analitica?dias={dias}&por={por}#diagnostico")

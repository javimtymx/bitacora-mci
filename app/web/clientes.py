"""Sección Clientes: agenda mensual de sesiones, pendientes de clientes (kanban de Operaciones) y benchmark de competidores."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    AGENDA_ESTADOS,
    AGENDA_TIPOS,
    INSIGHT_TIPOS,
    TICKET_ESTADOS,
    PENDIENTE_ESTADOS,
    PENDIENTE_TIPOS,
    AgendaCliente,
    Area,
    ArchivoCliente,
    Competidor,
    PendienteCliente,
    SesionCliente,
    SesionCompetidor,
    Usuario,
    utcnow,
)
from ..services import agenda_avisos, almacen, benchmark, clientes as cl, ia, tickets as tk, vozcliente
from ..services.evidencias import PERMITIDOS, EvidenciaInvalida
from ..services.util import auditar, hoy
from .base import OptDate, OptInt, flash, ir, render, requiere_lider, usuario_actual
from .voz import MAX_BYTES, MAX_BYTES_S3, _clientes

router = APIRouter()
VISIBLES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}


def _gente(db: Session) -> list[Usuario]:
    """Personas asignables: primero Operaciones, luego el resto del equipo activo."""
    us = list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre)))
    return sorted(us, key=lambda x: (0 if x.area and x.area.clave == "operaciones" else 1, x.nombre))


def _areas(db: Session) -> list[Area]:
    return list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)))


def _area_operaciones(db: Session) -> Area | None:
    return db.scalar(select(Area).where(Area.clave == "operaciones"))


def _quiere_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


async def _leer_transcripcion(u: Usuario, archivo: UploadFile | None, subidas: list[str]) -> str:
    if subidas:
        from ..services import subidas as subidas_srv
        try:
            info = subidas_srv.recibir(subidas[0], u.id, "transcripcion")
        except subidas_srv.SubidaInvalida as e:
            raise HTTPException(400, str(e)) from e
        if info["bytes"] > MAX_BYTES_S3:
            raise HTTPException(400, "La transcripción pasa de 25 MB de texto; divídela en partes.")
        raw = almacen.leer(info["ruta"])
        almacen.borrar(info["ruta"])
    elif archivo is not None and archivo.filename:
        raw = await archivo.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise HTTPException(400, "La transcripción pasa de 2 MB; con el almacén S3 activo se aceptan archivos más grandes.")
        if not archivo.filename.lower().endswith((".txt", ".vtt", ".srt", ".md")):
            raise HTTPException(400, "Sube la transcripción como archivo de texto (.txt, .vtt, .srt).")
    else:
        raise HTTPException(400, "Adjunta la transcripción.")
    texto = vozcliente.decodificar(raw).strip()
    if len(texto.split()) < 80:
        raise HTTPException(400, "La transcripción es demasiado corta para analizarla (menos de 80 palabras).")
    return texto


# ================================================================ agenda del mes

@router.get("/clientes")
def inicio():
    return ir("/clientes/agenda")


def _puede_editar_agenda(u: Usuario, a: AgendaCliente) -> bool:
    return u.es_lider or u.es_direccion or a.responsable_id == u.id or a.creado_por == u.id


@router.get("/clientes/agenda")
def agenda(request: Request, mes: str = "", area: OptInt = None, db: Session = Depends(get_db),
           u: Usuario = Depends(usuario_actual)):
    mes = cl.mes_valido(mes)
    ses = cl.agenda_mes(db, mes, area)
    semanas: dict[int, list] = {}
    for a in ses:
        semanas.setdefault(a.fecha.isocalendar()[1], []).append(a)
    ops = _area_operaciones(db)
    return render(request, "clientes_agenda.html", u, "clientes-agenda", mes=mes, etiqueta=cl.etiqueta_mes(mes),
                  ant=cl.mes_mover(mes, -1), sig=cl.mes_mover(mes, 1), sesiones=ses, semanas=sorted(semanas.items()),
                  res=cl.resumen_mes(ses), area_sel=area, areas=_areas(db), gente=_gente(db), tipos=AGENDA_TIPOS,
                  estado_visible=cl.estado_visible, nombre_cliente=cl.nombre_cliente, flag_minuta=cl.flag_minuta,
                  catalogo=_clientes(db) if u.puede_confidencial else [], area_def=ops.id if ops else u.area_id,
                  hay_anterior=bool(cl.agenda_mes(db, cl.mes_mover(mes, -1))), flags=agenda_avisos.flags(db, ses))


@router.post("/clientes/agenda")
def crear_agenda(request: Request, fondo: BackgroundTasks, tipo: str = Form(...), fecha: date = Form(...), hora: str = Form(""),
                 responsable_id: int = Form(...), area_id: OptInt = Form(None), cliente: str = Form(""),
                 cliente_nombre: str = Form(""), puntos: str = Form(""), p_nombre: list[str] = Form([]),
                 p_email: list[str] = Form([]), p_lado: list[str] = Form([]), recurrencia: str = Form(""),
                 fecha_fin: OptDate = Form(None), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    if tipo not in AGENDA_TIPOS:
        raise HTTPException(400, "Tipo de sesión inválido.")
    rfc, nombre = "", cliente_nombre.strip()[:255]
    if cliente and "|" in cliente:
        rfc, nombre = cliente.split("|", 1)
    if not (rfc or nombre):
        flash(request, "Indica el cliente de la sesión.", "error")
        return ir(f"/clientes/agenda?mes={fecha:%Y-%m}")
    lista = [p.strip(" -•\t")[:300] for p in puntos.splitlines() if p.strip(" -•\t")][:20]
    if not lista:
        flash(request, "Escribe al menos un punto de la agenda: es contra lo que se confirma la ejecución.", "error")
        return ir(f"/clientes/agenda?mes={fecha:%Y-%m}")
    fechas = [fecha]
    serie = None
    if recurrencia in ("semanal", "mensual"):
        try:
            fechas = cl.fechas_serie(fecha, recurrencia, fecha_fin)
        except ValueError as e:
            flash(request, str(e), "error")
            return ir(f"/clientes/agenda?mes={fecha:%Y-%m}")
        import uuid
        serie = str(uuid.uuid4())
    ops = _area_operaciones(db)
    invitados = cl.limpiar_invitados(p_nombre, p_email, p_lado)
    creadas = []
    for i, f in enumerate(fechas, 1):
        a = AgendaCliente(mes=f"{f:%Y-%m}", tipo=tipo, area_id=area_id or (ops.id if ops else u.area_id),
                          responsable_id=responsable_id, cliente_rfc=rfc[:13], cliente_nombre=nombre[:255], fecha=f,
                          hora=hora[:5], puntos=list(lista), invitados=[dict(x) for x in invitados], creado_por=u.id,
                          historial=[], ejecucion={}, serie_id=serie, recurrencia=recurrencia if serie else None)
        cl.evento(a, u, f"Sesión programada para el {f:%d/%m/%Y}{' a las ' + hora if hora else ''}"
                  + (f" · cita {i} de {len(fechas)} de la serie {recurrencia}." if serie else "."))
        db.add(a)
        creadas.append(a)
    db.flush()
    a = creadas[0]
    auditar(db, "programar", "agenda_cliente", a.id, u.id, tipo=tipo, fecha=str(fecha), serie=serie, citas=len(creadas))
    db.commit()
    for x in creadas:  # invitación de calendario de lo próximo; las demás salen solas al acercarse (35 días)
        if (x.fecha - hoy()).days <= agenda_avisos.DIAS_INVITACION:
            fondo.add_task(agenda_avisos.enviar_en_fondo, x.id, "invitacion")
    if serie:
        flash(request, f"Serie {recurrencia} creada: {len(creadas)} citas hasta el {fechas[-1]:%d/%m/%Y}. Cada una tiene su "
                       f"transcripción obligatoria; la invitación de calendario sale {agenda_avisos.DIAS_INVITACION} días antes de cada cita.")
    else:
        flash(request, "Sesión agregada a la agenda del mes. Se envía la invitación de calendario al equipo de OneCore.")
    return ir(f"/clientes/agenda/{a.id}")


@router.post("/clientes/agenda/copiar")
def copiar_mes(request: Request, fondo: BackgroundTasks, mes: str = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    """Arma el mes con las sesiones del mes anterior (mismo cliente, tipo, agenda e invitados), un mes después."""
    import calendar
    mes = cl.mes_valido(mes)
    ya = {(a.cliente_rfc, a.cliente_nombre, a.tipo) for a in cl.agenda_mes(db, mes)}
    y, m = map(int, mes.split("-"))
    n, nuevas = 0, []
    for a in cl.agenda_mes(db, cl.mes_mover(mes, -1)):
        if a.estado == "cancelada" or a.serie_id or (a.cliente_rfc, a.cliente_nombre, a.tipo) in ya:
            continue
        f = date(y, m, min(a.fecha.day, calendar.monthrange(y, m)[1]))
        nueva = AgendaCliente(mes=mes, tipo=a.tipo, area_id=a.area_id, responsable_id=a.responsable_id, cliente_rfc=a.cliente_rfc,
                              cliente_nombre=a.cliente_nombre, fecha=f, hora=a.hora, puntos=list(a.puntos or []),
                              invitados=[{k: v for k, v in i.items() if k not in ("asistio", "detectado")} | {"asistio": False}
                                         for i in a.invitados or []], creado_por=u.id, historial=[], ejecucion={})
        cl.evento(nueva, u, f"Copiada de la sesión #{a.id:04d} del mes anterior.")
        db.add(nueva)
        n += 1
        nuevas.append(nueva)
    db.commit()
    for x in nuevas:
        fondo.add_task(agenda_avisos.enviar_en_fondo, x.id, "invitacion")
    flash(request, f"Se copiaron {n} sesiones del mes anterior. Ajusta fechas y agenda." if n else "No había sesiones nuevas que copiar.",
          "ok" if n else "error")
    return ir(f"/clientes/agenda?mes={mes}")


def _agenda(db: Session, aid: int) -> AgendaCliente:
    a = db.get(AgendaCliente, aid)
    if not a:
        raise HTTPException(404)
    return a


@router.get("/clientes/agenda/{aid}")
def ver_agenda(aid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = _agenda(db, aid)
    s = a.sesion
    return render(request, "clientes_agenda_ver.html", u, "clientes-agenda", a=a, s=s, tipos=AGENDA_TIPOS,
                  estados=AGENDA_ESTADOS, estado=cl.estado_visible(a), cliente=cl.nombre_cliente(u, a),
                  puede=_puede_editar_agenda(u, a), gente=_gente(db), lider=cl.puede_gestionar(u) or a.responsable_id == u.id,
                  flag_minuta=cl.flag_minuta(a),
                  atorado=bool(s and vozcliente.analisis_atorado(s)), ve_sesion=bool(s and (u.es_direccion or s.autor_id == u.id or
                                                                                          (u.rol == "lider" and u.area_id == s.area_id))),
                  propuestas=[p for p in a.pendientes if p.estado == "propuesta"],
                  pendientes=[p for p in a.pendientes if p.estado != "propuesta"], pend_estados=PENDIENTE_ESTADOS,
                  por_id={p.id: p for p in a.pendientes},
                  vence=cl.vence, fecha_def=hoy() + timedelta(days=5), prep=agenda_avisos.preparacion(db, a),
                  serie=list(db.scalars(select(AgendaCliente).where(AgendaCliente.serie_id == a.serie_id)
                                        .order_by(AgendaCliente.fecha))) if a.serie_id else [])


@router.post("/clientes/agenda/{aid}/editar")
def editar_agenda(aid: int, request: Request, hora: str = Form(""), responsable_id: int = Form(...), puntos: str = Form(""),
                  p_nombre: list[str] = Form([]), p_email: list[str] = Form([]), p_lado: list[str] = Form([]),
                  asistio: list[str] = Form([]), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = _agenda(db, aid)
    if not _puede_editar_agenda(u, a):
        raise HTTPException(403)
    lista = [p.strip(" -•\t")[:300] for p in puntos.splitlines() if p.strip(" -•\t")][:20]
    if a.sesion_id and lista != list(a.puntos or []):
        flash(request, "La agenda ya se contrastó con la transcripción; no se pueden cambiar los puntos.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    if not lista:
        flash(request, "La agenda necesita al menos un punto.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    antes = {"puntos": a.puntos, "invitados": [i["nombre"] for i in a.invitados or []], "responsable": a.responsable_id}
    a.hora, a.responsable_id, a.puntos = hora[:5], responsable_id, lista
    det = {cl._norm(i["nombre"]): i.get("detectado") for i in a.invitados or []}
    a.invitados = [{**i, "detectado": det.get(cl._norm(i["nombre"]), False)}
                   for i in cl.limpiar_invitados(p_nombre, p_email, p_lado, asistio)]
    cl.evento(a, u, "Se actualizaron agenda, invitados o asistencia.")
    auditar(db, "editar", "agenda_cliente", a.id, u.id, antes=antes)
    db.commit()
    flash(request, "Sesión actualizada.")
    return ir(f"/clientes/agenda/{a.id}")


@router.post("/clientes/agenda/{aid}/reprogramar")
def reprogramar(aid: int, request: Request, fondo: BackgroundTasks, fecha: date = Form(...), hora: str = Form(""), motivo: str = Form(""),
                db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = _agenda(db, aid)
    if not _puede_editar_agenda(u, a) or a.estado not in ("programada", "reprogramada"):
        raise HTTPException(403)
    if len(motivo.strip()) < 5:
        flash(request, "Escribe el motivo de la reprogramación.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    cl.evento(a, u, f"Reprogramada del {a.fecha:%d/%m/%Y} al {fecha:%d/%m/%Y}. Motivo: {motivo.strip()}", antes=str(a.fecha))
    a.fecha, a.hora, a.mes, a.estado = fecha, hora[:5], f"{fecha:%Y-%m}", "reprogramada"
    db.commit()
    fondo.add_task(agenda_avisos.enviar_en_fondo, a.id, "reprogramacion")
    flash(request, "Sesión reprogramada; el cambio queda en su historial.")
    return ir(f"/clientes/agenda/{a.id}")


@router.post("/clientes/agenda/{aid}/cancelar")
def cancelar(aid: int, request: Request, fondo: BackgroundTasks, motivo: str = Form(""), serie: bool = Form(False),
             db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = _agenda(db, aid)
    if not _puede_editar_agenda(u, a) or a.estado not in ("programada", "reprogramada"):
        raise HTTPException(403)
    if len(motivo.strip()) < 5:
        flash(request, "Escribe por qué se cancela.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    lista = [a]
    if serie and a.serie_id:  # esta y las siguientes de la serie que aún no ocurren
        lista = list(db.scalars(select(AgendaCliente).where(AgendaCliente.serie_id == a.serie_id, AgendaCliente.fecha >= a.fecha,
                                                            AgendaCliente.estado.in_(("programada", "reprogramada")))))
    for x in lista:
        x.estado = "cancelada"
        cl.evento(x, u, f"Cancelada{' (con el resto de la serie)' if len(lista) > 1 else ''}. Motivo: {motivo.strip()}")
    db.commit()
    for x in lista:
        if any(v["tipo"] in ("invitacion", "reprogramacion") for v in x.avisos or []):  # solo a quien ya la tenía en su calendario
            fondo.add_task(agenda_avisos.enviar_en_fondo, x.id, "cancelacion")
    flash(request, f"Se cancelaron {len(lista)} citas de la serie; quedan en la agenda como canceladas." if len(lista) > 1
          else "Sesión cancelada; queda en la agenda como cancelada.")
    return ir(f"/clientes/agenda?mes={a.mes}")


@router.post("/clientes/agenda/{aid}/transcripcion")
async def cargar_transcripcion(aid: int, request: Request, fondo: BackgroundTasks, archivo: UploadFile | None = File(None),
                               subidas: list[str] = Form([]), consentimiento: bool = Form(False),
                               db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = _agenda(db, aid)
    if not _puede_editar_agenda(u, a):
        raise HTTPException(403)
    if a.estado == "cancelada":
        raise HTTPException(400, "La sesión está cancelada.")
    if not consentimiento:
        flash(request, "Confirma que el cliente sabe que la sesión se grabó.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    if a.fecha > hoy():
        flash(request, "La sesión aún no ocurre; carga la transcripción después de la fecha programada.", "error")
        return ir(f"/clientes/agenda/{a.id}")
    texto = await _leer_transcripcion(u, archivo, subidas)
    s = a.sesion
    if s is None:
        s = SesionCliente(tipo="agenda", area_id=a.area_id, cliente_rfc=a.cliente_rfc, cliente_nombre=a.cliente_nombre,
                          fecha=a.fecha, participantes=", ".join(i["nombre"] for i in a.invitados or [])[:300],
                          participantes_det=[{"nombre": i["nombre"], "email": i.get("email", ""), "lado": i.get("lado", "cliente"),
                                              "origen": "captura"} for i in a.invitados or []],
                          consentimiento=True, autor_id=u.id, transcripcion="")
        db.add(s)
    vozcliente.guardar_texto(s, texto)
    db.flush()
    a.sesion_id, a.estado = s.id, "analizando"
    cl.evento(a, u, f"Se cargó la transcripción ({s.palabras} palabras).")
    auditar(db, "cargar", "sesion_cliente", s.id, u.id, agenda=a.id, palabras=s.palabras)
    db.commit()
    vozcliente.marcar_en_analisis(db, s)
    fondo.add_task(vozcliente.analizar_en_fondo, s.id)
    flash(request, "Transcripción guardada. GuardIA revisa la agenda punto por punto; esta pantalla se actualiza sola.")
    return ir(f"/clientes/agenda/{a.id}")


# ================================================================ pendientes de clientes (kanban de Operaciones)

def _pend(db: Session, pid: int) -> PendienteCliente:
    p = db.get(PendienteCliente, pid)
    if not p:
        raise HTTPException(404)
    return p


@router.get("/clientes/pendientes")
def pendientes(request: Request, persona: OptInt = None, tipo: str = "", q: str = "", cerrados: bool = False,
               db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    q = q.strip()
    if q:  # búsqueda: por folio (PC-0012 o 12) o por palabras; por omisión solo en lo activo
        excluir = () if cerrados else ("historico", "no_vigente", "resuelto")
        lista = [p for p in db.scalars(select(PendienteCliente).order_by(PendienteCliente.id.desc()))
                 if p.estado not in excluir and cl.coincide(p, q)]
    else:
        lista = list(db.scalars(select(PendienteCliente).where(PendienteCliente.estado.notin_(("historico", "no_vigente")))
                                .order_by(PendienteCliente.id.desc())))
        desde = utcnow() - timedelta(days=30)
        lista = [p for p in lista if p.estado != "resuelto" or (p.resuelto_en and p.resuelto_en >= desde)]
    if persona:
        lista = [p for p in lista if p.asignado_id == persona]
    if tipo in PENDIENTE_TIPOS:
        lista = [p for p in lista if p.tipo == tipo]
    fuera = [p for p in lista if p.estado in ("historico", "no_vigente")]  # resultados de búsqueda fuera del kanban
    orden = {"alta": 0, "media": 1, "baja": 2}
    cols = []
    for clave, titulo in cl.COLUMNAS:
        xs = sorted([p for p in lista if p.estado == clave],
                    key=lambda p: (orden.get(p.severidad, 1), p.fecha_compromiso or date.max, p.id))
        cols.append((clave, titulo, xs))
    abiertos = [p for p in lista if p.estado in ("pendiente", "en_curso", "revision", "delegado")]
    return render(request, "clientes_pendientes.html", u, "clientes-pendientes", columnas=cols, gente=_gente(db),
                  q=q, cerrados=cerrados, fuera=fuera, productos=tk.productos(db),
                  persona=persona, tipo=tipo, tipos=PENDIENTE_TIPOS, vence=cl.vence, nombre_cliente=cl.nombre_cliente,
                  lider=cl.puede_gestionar(u), catalogo=_clientes(db) if u.puede_confidencial else [], ve_hist=cl.ve_historico(u),
                  n_hist=db.query(PendienteCliente).filter(PendienteCliente.estado == "historico").count(),
                  n_quejas=sum(1 for p in abiertos if p.tipo == "queja"), n_tareas=sum(1 for p in abiertos if p.tipo == "tarea"),
                  n_vencidos=sum(1 for p in abiertos if cl.vence(p)[1] == "r"),
                  n_sospecha=sum(1 for p in lista if p.estado == "resuelto" and p.veredicto in ("dudoso", "no_corresponde")),
                  agendas=[a for a in cl.agenda_mes(db, hoy().strftime("%Y-%m")) + cl.agenda_mes(db, cl.mes_mover(hoy().strftime("%Y-%m"), -1))
                           if cl.ve_cliente(u, a)])


@router.post("/clientes/pendientes")
def crear_pendiente(request: Request, tipo: str = Form("tarea"), titulo: str = Form(...), detalle: str = Form(""),
                    severidad: str = Form("media"), cliente: str = Form(""), cliente_nombre: str = Form(""),
                    agenda_id: OptInt = Form(None), asignado_id: OptInt = Form(None), fecha_compromiso: OptDate = Form(None),
                    producto: str = Form(""), menu: str = Form(""),
                    db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    if len(titulo.strip()) < 5:
        flash(request, "Escribe qué hay que resolver.", "error")
        return ir("/clientes/pendientes")
    a = db.get(AgendaCliente, agenda_id) if agenda_id else None
    rfc, nombre = "", cliente_nombre.strip()[:255]
    if cliente and "|" in cliente:
        rfc, nombre = cliente.split("|", 1)
    if a:
        rfc, nombre = a.cliente_rfc, a.cliente_nombre
    lider = cl.puede_gestionar(u) or bool(a and a.responsable_id == u.id)
    p = PendienteCliente(tipo=tipo if tipo in PENDIENTE_TIPOS else "tarea", titulo=titulo.strip()[:200], detalle=detalle.strip()[:2000],
                         severidad=severidad if severidad in ("alta", "media", "baja") else "media", cliente_rfc=rfc[:13],
                         cliente_nombre=nombre[:255], origen="manual", agenda_id=a.id if a else None,
                         producto=tk.producto_valido(db, producto) if producto.strip() else "", menu=menu.strip()[:120],
                         sesion_id=a.sesion_id if a else None,
                         estado="pendiente" if lider and asignado_id else "propuesta",
                         asignado_id=asignado_id if lider else None, fecha_compromiso=fecha_compromiso if lider else None,
                         creado_por=u.id, historial=[])
    cl.evento(p, u, "Capturado a mano." + ("" if p.estado == "pendiente" else " Queda por decidir si pasa al kanban."))
    db.add(p)
    db.flush()
    auditar(db, "crear", "pendiente", p.id, u.id, tipo=p.tipo, estado=p.estado)
    db.commit()
    flash(request, f"{p.folio} creado." + ("" if p.estado == "pendiente" else " Queda «por decidir» hasta que el responsable decida si pasa al kanban."))
    return ir(f"/clientes/pendientes/{p.id}")


@router.get("/clientes/pendientes/historico")
def historico(request: Request, ver: str = "auditar", db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    if not cl.ve_historico(u):
        raise HTTPException(403, "El histórico lo revisan Customer Success, líderes y Dirección.")
    estado = "no_vigente" if ver == "no_vigentes" else "historico"
    lista = list(db.scalars(select(PendienteCliente).where(PendienteCliente.estado == estado).order_by(PendienteCliente.decidido_en.desc(),
                                                                                                     PendienteCliente.id.desc())))
    return render(request, "clientes_historico.html", u, "clientes-pendientes", lista=lista, ver=ver, gente=_gente(db),
                  tipos=PENDIENTE_TIPOS, nombre_cliente=cl.nombre_cliente, cs=cl.es_customer_success(u),
                  n_auditar=db.query(PendienteCliente).filter(PendienteCliente.estado == "historico").count(),
                  n_no_vigentes=db.query(PendienteCliente).filter(PendienteCliente.estado == "no_vigente").count(),
                  fecha_def=hoy() + timedelta(days=5))


@router.get("/clientes/pendientes/{pid}")
def ver_pendiente(pid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)
    return render(request, "clientes_pendiente.html", u, "clientes-pendientes", p=p, cliente=cl.nombre_cliente(u, p),
                  estados=PENDIENTE_ESTADOS, tipos=PENDIENTE_TIPOS, gente=_gente(db), lider=cl.puede_gestionar(u),
                  decide=cl.puede_decidir(u, p), cs=cl.es_customer_success(u), fecha_def=hoy() + timedelta(days=5),
                  catalogo_mod=tk.catalogo(db), estados_ticket=TICKET_ESTADOS,
                  puede_delegar=cl.puede_atender(u, p) and p.estado in ("pendiente", "en_curso", "revision")
                  and not (p.ticket and p.ticket.estado != "cerrado"),
                  atiende=cl.puede_atender(u, p), vence=cl.vence(p),
                  ve_sesion=bool(p.sesion and (u.es_direccion or p.sesion.autor_id == u.id or
                                               (u.rol == "lider" and u.area_id == p.sesion.area_id))))


@router.post("/clientes/pendientes/{pid}/confirmar")
def confirmar(pid: int, request: Request, asignado_id: int = Form(...), fecha_compromiso: date = Form(...),
              titulo: str = Form(""), tipo: str = Form(""), severidad: str = Form(""), volver: str = Form(""),
              motivo: str = Form(""), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    """Pasar al kanban: el operativo decide desde la minuta; Customer Success puede rescatar lo que quedó en histórico."""
    p = _pend(db, pid)
    if p.estado == "propuesta":
        if not cl.puede_decidir(u, p):
            raise HTTPException(403, "Decide el responsable de la sesión (o un líder).")
    elif p.estado == "historico":
        if not cl.es_customer_success(u):
            raise HTTPException(403, "Lo que quedó en histórico lo audita Customer Success.")
    else:
        raise HTTPException(400, "Ya está en el kanban.")
    if titulo.strip():
        p.titulo = titulo.strip()[:200]
    if tipo in PENDIENTE_TIPOS:
        p.tipo = tipo
    if severidad in ("alta", "media", "baja"):
        p.severidad = severidad
    asig = db.get(Usuario, asignado_id)
    desde_hist = p.estado == "historico"
    if desde_hist:
        p.auditoria_motivo, p.auditado_por, p.auditado_en = (motivo.strip() or "Customer Success lo pasó al kanban")[:1000], u.id, utcnow()
    else:
        p.decidido_por, p.decidido_en = u.id, utcnow()
    p.estado, p.asignado_id, p.fecha_compromiso = "pendiente", asignado_id, fecha_compromiso
    cl.evento(p, u, ("Customer Success lo rescató del histórico y lo pasó al kanban" if desde_hist else "Pasó al kanban")
              + f": asignado a {asig.nombre if asig else '—'} para el {fecha_compromiso:%d/%m/%Y}."
              + (f" Motivo: {motivo.strip()}" if motivo.strip() else ""))
    auditar(db, "pasar_kanban", "pendiente", p.id, u.id, asignado=asignado_id, desde=("historico" if desde_hist else "minuta"))
    db.commit()
    flash(request, f"{p.folio} pasó al kanban de seguimiento.")
    return ir(volver if volver.startswith("/clientes/") else f"/clientes/pendientes/{p.id}")


def _volver(volver: str, defecto: str) -> str:
    return volver if volver.startswith("/clientes/") else defecto


@router.post("/clientes/pendientes/{pid}/no-pasar")
def no_pasar(pid: int, request: Request, motivo: str = Form(""), volver: str = Form(""), db: Session = Depends(get_db),
             u: Usuario = Depends(usuario_actual)):
    """El operativo decide que no pasa al kanban: queda en histórico para que Customer Success lo audite."""
    p = _pend(db, pid)
    if p.estado != "propuesta":
        raise HTTPException(400, "Solo se decide sobre lo que está por decidir.")
    if not cl.puede_decidir(u, p):
        raise HTTPException(403, "Decide el responsable de la sesión (o un líder).")
    if len(motivo.strip()) < 5:
        flash(request, "Escribe por qué no pasa al kanban: Customer Success lo va a auditar.", "error")
        return ir(_volver(volver, f"/clientes/pendientes/{p.id}"))
    p.estado, p.decision_motivo, p.decidido_por, p.decidido_en = "historico", motivo.strip()[:1000], u.id, utcnow()
    cl.evento(p, u, f"No pasó al kanban; queda en histórico para auditoría de Customer Success. Motivo: {motivo.strip()}")
    auditar(db, "no_pasar", "pendiente", p.id, u.id, motivo=motivo.strip()[:300])
    db.commit()
    flash(request, f"{p.folio} quedó en el histórico; Customer Success lo audita.")
    return ir(_volver(volver, "/clientes/pendientes"))


@router.post("/clientes/pendientes/{pid}/no-vigente")
def no_vigente(pid: int, request: Request, motivo: str = Form(""), volver: str = Form(""), db: Session = Depends(get_db),
               u: Usuario = Depends(usuario_actual)):
    """Solo Customer Success da de baja algo del histórico (confirma que ya no es vigente). Sigue visible para Dirección."""
    p = _pend(db, pid)
    if p.estado != "historico":
        raise HTTPException(400, "Solo se confirma como no vigente lo que está en el histórico.")
    if not cl.es_customer_success(u):
        raise HTTPException(403, "Solo Customer Success confirma que una tarea ya no es vigente.")
    if len(motivo.strip()) < 5:
        flash(request, "Escribe por qué ya no es vigente.", "error")
        return ir(_volver(volver, "/clientes/pendientes/historico"))
    p.estado, p.auditoria_motivo, p.auditado_por, p.auditado_en = "no_vigente", motivo.strip()[:1000], u.id, utcnow()
    cl.evento(p, u, f"Customer Success confirmó que no es vigente. Motivo: {motivo.strip()}")
    auditar(db, "no_vigente", "pendiente", p.id, u.id, motivo=motivo.strip()[:300])
    db.commit()
    flash(request, f"{p.folio} confirmado como no vigente; queda en el histórico para Dirección.")
    return ir(_volver(volver, "/clientes/pendientes/historico"))


@router.post("/clientes/pendientes/{pid}/mover")
def mover(pid: int, request: Request, estado: str = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)

    def no(msg, **extra):
        if _quiere_json(request):
            return JSONResponse({"ok": False, "mensaje": msg, **extra})
        flash(request, msg, "error")
        return ir(f"/clientes/pendientes/{p.id}")

    if not cl.puede_atender(u, p):
        return no("Solo la persona asignada o un líder pueden moverlo.")
    if p.estado in ("propuesta", "historico", "no_vigente", "resuelto"):
        return no("Primero hay que decidir si pasa al kanban." if p.estado == "propuesta" else "No está en el kanban.", abrir=p.estado == "propuesta")
    if estado == "resuelto":
        return no("Para resolverlo describe la solución y sube la evidencia.", resolver=True)
    if estado not in ("pendiente", "en_curso", "revision"):
        return no("Estado inválido.")
    if not p.asignado_id:
        return no("Asígnalo a alguien antes de moverlo.")
    if estado != p.estado:
        cl.evento(p, u, f"Pasó de «{PENDIENTE_ESTADOS[p.estado]}» a «{PENDIENTE_ESTADOS[estado]}».")
        p.estado = estado
        db.commit()
    if _quiere_json(request):
        return JSONResponse({"ok": True})
    return ir(f"/clientes/pendientes/{p.id}")


@router.post("/clientes/pendientes/{pid}/modulo")
def modulo(pid: int, request: Request, producto: str = Form(""), menu: str = Form(""), db: Session = Depends(get_db),
           u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)
    if not (cl.puede_atender(u, p) or cl.puede_decidir(u, p)):
        raise HTTPException(403)
    antes = " › ".join(x for x in (p.producto, p.menu) if x)
    p.producto = tk.producto_valido(db, producto) if producto.strip() else ""
    p.menu = menu.strip()[:120]
    cl.evento(p, u, f"Módulo: {' › '.join(x for x in (p.producto, p.menu) if x) or 'sin módulo'}" + (f" (antes {antes})" if antes else ""))
    db.commit()
    return ir(f"/clientes/pendientes/{p.id}")


@router.post("/clientes/pendientes/{pid}/asignar")
def asignar(pid: int, request: Request, asignado_id: int = Form(...), fecha_compromiso: date = Form(...), motivo: str = Form(""),
            db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    p = _pend(db, pid)
    if p.estado not in ("pendiente", "en_curso", "revision"):
        raise HTTPException(400)
    asig = db.get(Usuario, asignado_id)
    if p.fecha_compromiso and fecha_compromiso != p.fecha_compromiso and len(motivo.strip()) < 5:
        flash(request, "Cambiar la fecha compromiso pide un motivo.", "error")
        return ir(f"/clientes/pendientes/{p.id}")
    cl.evento(p, u, f"Asignado a {asig.nombre if asig else '—'} · fecha compromiso {fecha_compromiso:%d/%m/%Y}"
              + (f". Motivo: {motivo.strip()}" if motivo.strip() else "."))
    p.asignado_id, p.fecha_compromiso = asignado_id, fecha_compromiso
    db.commit()
    flash(request, "Asignación actualizada.")
    return ir(f"/clientes/pendientes/{p.id}")


@router.post("/clientes/pendientes/{pid}/nota")
def nota(pid: int, request: Request, texto: str = Form(""), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)
    if not cl.puede_atender(u, p):
        raise HTTPException(403)
    if texto.strip():
        cl.evento(p, u, texto.strip(), nota=True)
        db.commit()
    return ir(f"/clientes/pendientes/{p.id}")


async def _subir(db: Session, p: PendienteCliente, archivos: list[UploadFile], subidas_: list[str], u: Usuario) -> tuple[int, list[str]]:
    from ..services import subidas as subidas_srv
    from ..services.evidencias import MAX_BYTES as MAXB
    n, errores = 0, []
    if len(p.evidencias) + len([a for a in archivos or [] if a and a.filename]) + len(subidas_ or []) > 20:
        return 0, ["Máximo 20 archivos de evidencia por pendiente."]
    for token in subidas_ or []:
        try:
            cl.registrar_subida(db, "pendiente", p.id, subidas_srv.recibir(token, u.id, "evidencia"), u)
            n += 1
        except (subidas_srv.SubidaInvalida, EvidenciaInvalida) as e:
            errores.append(str(e))
    for a in archivos or []:
        if a and a.filename:
            try:
                cl.guardar_archivo(db, "pendiente", p.id, a.filename, await a.read(MAXB + 1), u)
                n += 1
            except EvidenciaInvalida as e:
                errores.append(str(e))
    db.expire(p, ["evidencias"])
    return n, errores


@router.post("/clientes/pendientes/{pid}/evidencias")
async def subir_evidencias(pid: int, request: Request, fondo: BackgroundTasks, archivos: list[UploadFile] = File([]),
                           subidas: list[str] = Form([]), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)
    if not cl.puede_atender(u, p) or p.estado == "propuesta":
        raise HTTPException(403)
    n, errores = await _subir(db, p, archivos, subidas, u)
    if errores:
        db.rollback()
        flash(request, " ".join(errores), "error")
        return ir(f"/clientes/pendientes/{p.id}")
    if n:
        cl.evento(p, u, f"Subió {n} archivo(s) de evidencia.")
    if n and p.estado == "resuelto" and ia.cliente().disponible:
        p.veredicto = "verificando"
        fondo.add_task(cl.verificar_en_fondo, p.id)
    db.commit()
    flash(request, f"Se cargaron {n} archivo(s)." if n else "No elegiste archivos.", "ok" if n else "error")
    return ir(f"/clientes/pendientes/{p.id}")


@router.post("/clientes/pendientes/{pid}/resolver")
async def resolver(pid: int, request: Request, fondo: BackgroundTasks, resolucion: str = Form(""),
                   archivos: list[UploadFile] = File([]), subidas: list[str] = Form([]), db: Session = Depends(get_db),
                   u: Usuario = Depends(usuario_actual)):
    p = _pend(db, pid)
    if not cl.puede_atender(u, p) or p.estado not in ("pendiente", "en_curso", "revision"):
        raise HTTPException(403)
    if len(resolucion.strip()) < 10:
        flash(request, "Describe cómo se resolvió (mínimo una frase).", "error")
        return ir(f"/clientes/pendientes/{p.id}#resolver")
    n, errores = await _subir(db, p, archivos, subidas, u)
    if errores:
        db.rollback()
        flash(request, " ".join(errores), "error")
        return ir(f"/clientes/pendientes/{p.id}#resolver")
    por_soporte = bool(p.ticket and p.ticket.estado == "cerrado")
    if not p.evidencias and not por_soporte:
        db.rollback()
        flash(request, "Para resolverlo sube al menos un archivo que demuestre la solución.", "error")
        return ir(f"/clientes/pendientes/{p.id}#resolver")
    p.estado, p.resolucion, p.resuelto_en = "resuelto", resolucion.strip()[:3000], utcnow()
    cl.evento(p, u, f"Resuelto: {p.resolucion[:300]}")
    if por_soporte and not p.evidencias:  # la evidencia es el ticket de soporte cerrado y aceptado por quien lo levantó
        p.veredicto = "soporte"
    elif ia.cliente().disponible:
        p.veredicto = "verificando"
        fondo.add_task(cl.verificar_en_fondo, p.id)
    else:
        p.veredicto = "sin_verificar"
    auditar(db, "resolver", "pendiente", p.id, u.id, evidencias=len(p.evidencias))
    db.commit()
    flash(request, f"{p.folio} resuelto." + (" GuardIA está revisando la evidencia." if p.veredicto == "verificando" else ""))
    return ir(f"/clientes/pendientes/{p.id}")


@router.post("/clientes/pendientes/{pid}/reabrir")
def reabrir(pid: int, request: Request, motivo: str = Form(""), db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    p = _pend(db, pid)
    if p.estado != "resuelto":
        raise HTTPException(400)
    if len(motivo.strip()) < 5:
        flash(request, "Escribe por qué se reabre.", "error")
        return ir(f"/clientes/pendientes/{p.id}")
    p.estado, p.resuelto_en = "en_curso", None
    cl.evento(p, u, f"Reabierto. Motivo: {motivo.strip()}")
    db.commit()
    flash(request, f"{p.folio} reabierto.")
    return ir(f"/clientes/pendientes/{p.id}")


def _archivo(db: Session, xid: int, entidad: str) -> ArchivoCliente:
    a = db.get(ArchivoCliente, xid)
    if not a or a.entidad != entidad:
        raise HTTPException(404)
    return a


def _servir(a: ArchivoCliente, ver: int):
    en_linea = bool(ver) and a.tipo in VISIBLES
    if (a.bytes or 0) > 20 * 1024 * 1024 and (url := almacen.url_descarga(a.ruta, a.nombre, a.tipo, en_linea=en_linea)):
        return RedirectResponse(url, status_code=302)
    try:
        contenido = almacen.leer(a.ruta)
    except (OSError, almacen.AlmacenError):
        raise HTTPException(404, "El archivo ya no está en el servidor.")
    return Response(contenido, media_type=a.tipo or "application/octet-stream",
                    headers={"Content-Disposition": f"{'inline' if en_linea else 'attachment'}; filename*=UTF-8''{quote(a.nombre)}",
                             "X-Content-Type-Options": "nosniff"})


@router.get("/clientes/pendientes/archivos/{xid}")
def archivo_pendiente(xid: int, ver: int = 0, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    return _servir(_archivo(db, xid, "pendiente"), ver)


@router.post("/clientes/pendientes/archivos/{xid}/retirar")
def retirar_archivo(xid: int, request: Request, fondo: BackgroundTasks, motivo: str = Form(""), db: Session = Depends(get_db),
                    u: Usuario = Depends(usuario_actual)):
    a = _archivo(db, xid, "pendiente")
    p = _pend(db, a.entidad_id)
    if a.retirada or not (cl.puede_gestionar(u) or a.subido_por == u.id or p.asignado_id == u.id):
        raise HTTPException(403)
    if len(motivo.strip()) < 5:
        flash(request, "Escribe por qué se retira la evidencia.", "error")
        return ir(f"/clientes/pendientes/{p.id}")
    cl.retirar_archivo(db, a, u, motivo)
    cl.evento(p, u, f"Retiró la evidencia «{a.nombre}». Motivo: {motivo.strip()}")
    db.flush()
    db.expire(p, ["evidencias", "evidencias_retiradas"])
    if p.estado == "resuelto":
        if p.evidencias and ia.cliente().disponible:
            p.veredicto = "verificando"
            fondo.add_task(cl.verificar_en_fondo, p.id)
        else:
            p.veredicto = "sin_verificar"
            p.verificacion = {**(p.verificacion or {}), "error": "Se retiró la evidencia. Sube la correcta y GuardIA la revisará."}
    db.commit()
    flash(request, f"Se retiró «{a.nombre}»; queda en el historial.")
    return ir(f"/clientes/pendientes/{p.id}")


# ================================================================ benchmark de competidores

def _puede_ver_trans(u: Usuario, s: SesionCompetidor) -> bool:
    return u.es_direccion or s.autor_id == u.id


@router.get("/clientes/benchmark")
def panel_benchmark(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    con = benchmark.consolidado(db)
    return render(request, "clientes_benchmark.html", u, "clientes-benchmark", con=con, svg=benchmark.svg_lienzo(con["lienzo"]),
                  tipos=INSIGHT_TIPOS)


@router.post("/clientes/benchmark/sesiones")
async def crear_sesion_competidor(request: Request, fondo: BackgroundTasks, competidor_id: OptInt = Form(None),
                                  competidor_nuevo: str = Form(""), sitio: str = Form(""), fecha: date = Form(...),
                                  titulo: str = Form(""), contexto: str = Form(""), participantes: str = Form(""),
                                  archivo: UploadFile | None = File(None), subidas: list[str] = Form([]),
                                  pantallas: list[UploadFile] = File([]), db: Session = Depends(get_db),
                                  u: Usuario = Depends(requiere_lider)):
    comp = db.get(Competidor, competidor_id) if competidor_id else None
    if not comp:
        nombre = competidor_nuevo.strip()[:160]
        if len(nombre) < 2:
            flash(request, "Elige el competidor o escribe su nombre.", "error")
            return ir("/clientes/benchmark")
        comp = db.scalar(select(Competidor).where(Competidor.nombre == nombre)) or Competidor(nombre=nombre, sitio=sitio.strip()[:255])
        db.add(comp)
        db.flush()
    texto = await _leer_transcripcion(u, archivo, subidas)
    s = SesionCompetidor(competidor_id=comp.id, fecha=fecha, titulo=titulo.strip()[:200], contexto=contexto.strip()[:3000],
                         participantes=participantes.strip()[:300], autor_id=u.id, resultado={})
    vozcliente.guardar_texto(s, texto)
    db.add(s)
    db.flush()
    errores = []
    for a in [x for x in pantallas or [] if x and x.filename][:8]:
        try:
            cl.guardar_archivo(db, "benchmark", s.id, a.filename, await a.read(20 * 1024 * 1024 + 1), u, cl.PERMITIDOS_PANTALLA)
        except EvidenciaInvalida as e:
            errores.append(str(e))
    if errores:
        db.rollback()
        flash(request, " ".join(errores), "error")
        return ir("/clientes/benchmark")
    s.estado = "analizando"
    s.resultado = {"desde": vozcliente.utcnow_iso()}
    auditar(db, "cargar", "sesion_competidor", s.id, u.id, competidor=comp.nombre, palabras=s.palabras)
    db.commit()
    fondo.add_task(benchmark.analizar_en_fondo, s.id)
    flash(request, "Transcripción guardada. GuardIA arma el lienzo de propuesta de valor, el lienzo estratégico y la matriz ERIC.")
    return ir(f"/clientes/benchmark/sesiones/{s.id}")


def _sesion_comp(db: Session, sid: int) -> SesionCompetidor:
    s = db.get(SesionCompetidor, sid)
    if not s:
        raise HTTPException(404)
    return s


@router.get("/clientes/benchmark/sesiones/{sid}")
def ver_sesion_competidor(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    s = _sesion_comp(db, sid)
    lienzo = {"factores": [f["factor"] for f in (s.resultado or {}).get("factores", [])],
              "claves": [f["factor"] for f in (s.resultado or {}).get("factores", [])],
              "onecore": {f["factor"]: f["onecore"] for f in (s.resultado or {}).get("factores", [])},
              "series": [{"nombre": s.competidor.nombre, "color": benchmark.COLORES[0],
                          "valores": {f["factor"]: f["competidor"] for f in (s.resultado or {}).get("factores", [])}}]}
    return render(request, "clientes_benchmark_sesion.html", u, "clientes-benchmark", s=s, svg=benchmark.svg_lienzo(lienzo, 900, 280),
                  tipos=INSIGHT_TIPOS, ve_trans=_puede_ver_trans(u, s), atorado=vozcliente.analisis_atorado(s))


@router.post("/clientes/benchmark/sesiones/{sid}/analizar")
def reanalizar_competidor(sid: int, request: Request, fondo: BackgroundTasks, db: Session = Depends(get_db),
                          u: Usuario = Depends(requiere_lider)):
    s = _sesion_comp(db, sid)
    s.estado = "analizando"
    s.resultado = {k: v for k, v in (s.resultado or {}).items() if k != "error"} | {"desde": vozcliente.utcnow_iso()}
    db.commit()
    fondo.add_task(benchmark.analizar_en_fondo, s.id)
    flash(request, "GuardIA está analizando de nuevo la sesión.")
    return ir(f"/clientes/benchmark/sesiones/{s.id}")


@router.get("/clientes/benchmark/sesiones/{sid}/transcripcion")
def transcripcion_competidor(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    s = _sesion_comp(db, sid)
    if not _puede_ver_trans(u, s):
        raise HTTPException(403, "La transcripción completa solo la ven Dirección y quien la cargó.")
    auditar(db, "ver_transcripcion", "sesion_competidor", s.id, u.id)
    db.commit()
    return render(request, "clientes_benchmark_trans.html", u, "clientes-benchmark", s=s, texto=vozcliente.leer_texto(s))


@router.get("/clientes/benchmark/archivos/{xid}")
def archivo_benchmark(xid: int, ver: int = 0, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    return _servir(_archivo(db, xid, "benchmark"), ver)


# ================================================================ recurrencias de servicio (frecuencia × impacto)

@router.get("/clientes/servicio")
def servicio(request: Request, ver: OptInt = None, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    from ..models import AnalisisServicio
    from ..services import servicio as sv
    lista = list(db.scalars(select(AnalisisServicio).order_by(AnalisisServicio.id.desc()).limit(24)))
    an = db.get(AnalisisServicio, ver) if ver else (lista[0] if lista else None)
    r = (an.resultado or {}) if an else {}
    return render(request, "clientes_servicio.html", u, "clientes-servicio", an=an, r=r, lista=lista,
                  svg=sv.svg(r.get("temas") or [], r.get("umbral_clientes") or 2) if an and an.estado == "listo" else {},
                  cuadrantes=sv.CUADRANTES, desde_def=hoy() - timedelta(days=90), hasta_def=hoy(),
                  atorado=bool(an and an.estado == "analizando" and (utcnow() - an.creado).total_seconds() > 900))


@router.post("/clientes/servicio")
def servicio_generar(request: Request, fondo: BackgroundTasks, desde: date = Form(...), hasta: date = Form(...),
                     db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    from ..models import AnalisisServicio
    from ..services import servicio as sv
    if hasta < desde:
        flash(request, "La fecha final debe ser posterior a la inicial.", "error")
        return ir("/clientes/servicio")
    an = AnalisisServicio(desde=desde, hasta=hasta, autor_id=u.id, resultado={})
    db.add(an)
    db.commit()
    fondo.add_task(sv.analizar_en_fondo, an.id)
    flash(request, "GuardIA está compilando las sesiones del periodo; la pantalla se actualiza sola.")
    return ir(f"/clientes/servicio?ver={an.id}")

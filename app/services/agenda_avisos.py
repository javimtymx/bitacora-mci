"""Avisos internos de la agenda de clientes: invitación de calendario, recordatorio 5 días antes y escalación.

Todo es comunicación interna de OneCore: nunca se escribe a correos del cliente.
- Al programar (o reprogramar/cancelar) una sesión: correo con invitación iCalendar al responsable, a los invitados de
  OneCore y a quien tiene activado «avisos de la agenda» en su perfil (por omisión, Customer Success).
- 5 días antes: recordatorio con la bandera de preparación (verde/amarillo/rojo) según los pendientes del cliente.
  En rojo se copia a Dirección General, Dirección Comercial y Dirección de Operaciones.
- Cada correo recuerda que la transcripción es obligatoria y resalta acuerdos y pendientes de sesiones anteriores.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import PUESTOS_ESCALACION, AgendaCliente, PendienteCliente, Usuario, utcnow
from . import correo
from .util import hoy

log = logging.getLogger("bitacora.agenda")

DIAS_RECORDATORIO = 5
BANDERA = {"verde": "VERDE · vamos preparados", "amarillo": "AMARILLO · hay pendientes por cerrar antes de la sesión",
           "rojo": "ROJO · hay pendientes vencidos con el cliente"}
ABIERTOS = ("propuesta", "pendiente", "en_curso", "revision")


def _mismo_cliente(a: AgendaCliente):
    if a.cliente_rfc:
        return PendienteCliente.cliente_rfc == a.cliente_rfc
    return PendienteCliente.cliente_nombre == a.cliente_nombre


def preparacion(db: Session, a: AgendaCliente) -> dict:
    """¿Vamos preparados? Pendientes abiertos del cliente, acuerdos y puntos sin cubrir de la sesión anterior."""
    desde = utcnow() - timedelta(days=180)
    pend = list(db.scalars(select(PendienteCliente).where(_mismo_cliente(a), PendienteCliente.creado >= desde,
                                                          PendienteCliente.estado.notin_(("historico", "no_vigente")))))
    abiertos = [p for p in pend if p.estado in ABIERTOS]
    resueltos = [p for p in pend if p.estado == "resuelto"]
    vencidos = [p for p in abiertos if p.fecha_compromiso and p.fecha_compromiso < hoy()]
    sin_asignar_altas = [p for p in abiertos if p.estado == "propuesta" and p.severidad == "alta"]
    antes_de_sesion = [p for p in abiertos if p not in vencidos and p not in sin_asignar_altas
                       and (p.fecha_compromiso is None or p.fecha_compromiso <= a.fecha)]
    cond = [AgendaCliente.cliente_rfc == a.cliente_rfc] if a.cliente_rfc else [AgendaCliente.cliente_nombre == a.cliente_nombre]
    previa = db.scalar(select(AgendaCliente).where(*cond, AgendaCliente.id != a.id, AgendaCliente.fecha <= a.fecha,
                                                   AgendaCliente.sesion_id.is_not(None))
                       .order_by(AgendaCliente.fecha.desc()).limit(1))
    acuerdos, sin_cubrir, minuta, flag_min = [], [], {}, None
    if previa and previa.sesion and previa.sesion.estado == "analizada":
        from .clientes import flag_minuta
        minuta = (previa.ejecucion or {}).get("minuta") or {}
        por_id = {p.id: p for p in previa.pendientes}
        for x in minuta.get("acuerdos", []):
            if x.get("quien") == "onecore":
                p = por_id.get(x.get("pendiente_id"))
                acuerdos.append({**x, "texto": x.get("que", ""), "fecha": x.get("cuando", ""),
                                 "estado": p.estado if p else None, "folio": p.folio if p else None})
        sin_cubrir = [p["punto"] for p in (previa.ejecucion or {}).get("puntos", []) if p.get("cubierto") == "no"]
        flag_min = flag_minuta(previa)
    if vencidos or sin_asignar_altas:
        flag = "rojo"
    elif antes_de_sesion or sin_cubrir:
        flag = "amarillo"
    else:
        flag = "verde"
    total = len(abiertos) + len(resueltos)
    return {"flag": flag, "texto": BANDERA[flag], "abiertos": abiertos, "vencidos": vencidos, "antes": antes_de_sesion,
            "altas_sin_asignar": sin_asignar_altas, "resueltos": len(resueltos), "previa": previa, "acuerdos": acuerdos,
            "sin_cubrir": sin_cubrir, "minuta": minuta, "flag_minuta": flag_min, "cumplimiento": round(len(resueltos) / total * 100) if total else None}


# ---------------------------------------------------------------- destinatarios (solo OneCore)

def _correos_internos(db: Session, a: AgendaCliente) -> list[str]:
    us = [a.responsable] if a.responsable and a.responsable.activo else []
    us += [x for x in db.scalars(select(Usuario).where(Usuario.activo.is_(True), Usuario.recibe_correos.is_(True))) if x.recibe_agenda]
    out = {x.email.lower() for x in us if x.email}
    out |= {i["email"].lower() for i in a.invitados or [] if i.get("lado") == "onecore" and i.get("email")}
    return sorted(out)


def _escalacion(db: Session) -> list[str]:
    return sorted({x.email.lower() for x in db.scalars(select(Usuario).where(Usuario.activo.is_(True),
                                                                            Usuario.puesto.in_(PUESTOS_ESCALACION))) if x.email})


# ---------------------------------------------------------------- invitación iCalendar

def _ics_txt(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


SEMAFORO = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}


def descripcion_corta(a: AgendaCliente, prep: dict) -> str:
    """Recordatorio pequeño para el calendario: semáforo y pendientes del cliente. Sin liga de Meet: cada quien la crea."""
    s = get_settings()
    l = [f"Semáforo de preparación: {prep['flag'].upper()}",
         f"Pendientes abiertos con el cliente: {len(prep['abiertos'])} · vencidos: {len(prep['vencidos'])}",
         "Transcripción obligatoria al terminar.",
         "Recordatorio interno de la Bitácora MCI (sin liga de reunión: créala en tu calendario).",
         f"{s.base_url}/clientes/agenda/{a.id}"]
    return "\n".join(l)


def ics(a: AgendaCliente, metodo: str, secuencia: int, asistentes: list[str], descripcion: str, cliente: str,
        flag: str = "") -> str:
    """Evento de 1 minuto a la hora de la sesión: solo agenda al grupo y muestra el semáforo; no lleva liga de reunión."""
    s = get_settings()
    hh, mm = (a.hora or "09:00").split(":") if a.hora else ("09", "00")
    inicio = datetime(a.fecha.year, a.fecha.month, a.fecha.day, int(hh), int(mm))
    fin = inicio + timedelta(minutes=1)
    org = (s.smtp_from or "bitacora@onecore.mx").split("<")[-1].strip(">").strip()
    lineas = ["BEGIN:VCALENDAR", "PRODID:-//OneCore//Bitacora MCI//ES", "VERSION:2.0", "CALSCALE:GREGORIAN", f"METHOD:{metodo}",
              "BEGIN:VEVENT", f"UID:agenda-{a.id}@mci.onecore.mx", f"SEQUENCE:{secuencia}",
              f"DTSTAMP:{utcnow():%Y%m%dT%H%M%SZ}",
              f"DTSTART;TZID=America/Mexico_City:{inicio:%Y%m%dT%H%M%S}", f"DTEND;TZID=America/Mexico_City:{fin:%Y%m%dT%H%M%S}",
              f"SUMMARY:{_ics_txt((SEMAFORO.get(flag, '') + ' ' if flag else '') + 'Sesión ' + ('operativa' if a.tipo == 'operativa' else 'de resultado mensual') + ' · ' + cliente)}",
              f"DESCRIPTION:{_ics_txt(descripcion[:1800])}", f"ORGANIZER;CN=Bitácora MCI:mailto:{org}",
              f"STATUS:{'CANCELLED' if metodo == 'CANCEL' else 'CONFIRMED'}"]
    lineas += [f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{e}" for e in asistentes]
    if metodo != "CANCEL":
        lineas += ["BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", "DESCRIPTION:Sesión con cliente", "END:VALARM"]
    lineas += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lineas) + "\r\n"


# ---------------------------------------------------------------- cuerpo del correo

def _cuerpo(a: AgendaCliente, prep: dict, cliente: str, encabezado: str) -> str:
    tipo = "operativa" if a.tipo == "operativa" else "de resultado mensual"
    l = [encabezado, "",
         f"Cliente: {cliente} · Sesión {tipo}",
         f"Fecha: {a.fecha:%d/%m/%Y}{' a las ' + a.hora + ' h' if a.hora else ''} · Responsable: {a.responsable.nombre}", "",
         f"PREPARACIÓN: {prep['texto'].upper()}"
         + (f" · Calificación de cumplimiento con el cliente: {prep['cumplimiento']}% ({prep['resueltos']} resueltos)"
            if prep["cumplimiento"] is not None else ""), "", "Agenda:"]
    l += [f"- {p}" for p in a.puntos or []]
    if prep["vencidos"]:
        l += ["", "PENDIENTES VENCIDOS CON EL CLIENTE (atender antes de la sesión):"]
        l += [f"- {p.folio} · {p.titulo} · venció el {p.fecha_compromiso:%d/%m} · {p.asignado.nombre if p.asignado else 'sin asignar'}"
              for p in prep["vencidos"]]
    if prep["altas_sin_asignar"]:
        l += ["", "QUEJAS DE SEVERIDAD ALTA SIN CONFIRMAR NI ASIGNAR:"] + [f"- {p.folio} · {p.titulo}" for p in prep["altas_sin_asignar"]]
    if prep["antes"]:
        l += ["", "Pendientes abiertos que deben cerrarse antes de la sesión:"]
        l += [f"- {p.folio} · {p.titulo} · {('vence ' + p.fecha_compromiso.strftime('%d/%m')) if p.fecha_compromiso else 'sin fecha'}"
              f" · {p.asignado.nombre if p.asignado else 'sin asignar'}" for p in prep["antes"]]
    if prep["previa"]:
        fm = prep.get("flag_minuta")
        l += ["", f"MINUTA DE LA SESIÓN ANTERIOR ({prep['previa'].fecha:%d/%m/%Y})" + (f" · estado: {fm[1]}" if fm else "") + ":"]
        est = {"resuelto": "RESUELTO", "historico": "no pasó al kanban", "no_vigente": "no vigente", "propuesta": "POR DECIDIR"}
        l += [f"- Acuerdo de OneCore: {x['texto']}{' · ' + x['fecha'] if x.get('fecha') else ''}"
              + (f" → {x['folio']} {est.get(x['estado'], 'ABIERTO')}" if x.get("folio") else " → sin tarea")
              for x in prep["acuerdos"]] or ["- Sin acuerdos de OneCore registrados."]
        l += [f"- Decisión: {x['texto']}" for x in (prep.get("minuta") or {}).get("decisiones", [])]
        l += [f"- Riesgo: {x['texto']}" for x in (prep.get("minuta") or {}).get("riesgos", [])]
        l += [f"- Tema abierto: {x['texto']}" for x in (prep.get("minuta") or {}).get("temas_abiertos", [])]
        l += [f"- Punto que no se cubrió: {p}" for p in prep["sin_cubrir"]]
    inv = [i["nombre"] + (" (cliente)" if i.get("lado") != "onecore" else "") for i in a.invitados or []]
    if inv:
        l += ["", "Invitados: " + ", ".join(inv)]
    l += ["", "La transcripción de la sesión es OBLIGATORIA: cárgala en la bitácora el mismo día. Con ella GuardIA confirma "
              "que la agenda se ejecutó, registra la asistencia y propone las quejas y tareas para Operaciones.",
          "", "Comunicación interna de OneCore: no reenvíes este correo al cliente."]
    return "\n".join(l)


def _registrar(a: AgendaCliente, tipo: str, para: list[str], cc: list[str], flag: str, estado: str) -> None:
    a.avisos = list(a.avisos or []) + [{"tipo": tipo, "en": utcnow().isoformat(timespec="seconds"), "fecha": a.fecha.isoformat(),
                                        "para": para, "cc": cc, "flag": flag, "estado": estado}]


def _secuencia(a: AgendaCliente) -> int:
    return sum(1 for x in a.avisos or [] if x["tipo"] in ("invitacion", "reprogramacion", "cancelacion"))


def enviar(db: Session, a: AgendaCliente, tipo: str) -> dict:
    """tipo: invitacion | reprogramacion | cancelacion | recordatorio. Nunca lanza: el resultado queda en a.avisos."""
    from .clientes import evento
    s = get_settings()
    cliente = a.cliente_nombre or a.cliente_rfc or "Cliente"
    prep = preparacion(db, a)
    para = _correos_internos(db, a)
    if not para:
        return {"estado": "sin_destinatarios"}
    dias = (a.fecha - hoy()).days
    cc = _escalacion(db) if prep["flag"] == "rojo" and (tipo == "recordatorio" or (tipo != "cancelacion" and dias <= DIAS_RECORDATORIO)) else []
    enc = {"invitacion": "Se programó una sesión con cliente. Agrégala a tu calendario con la invitación adjunta.",
           "reprogramacion": "La sesión con el cliente cambió de fecha. La invitación adjunta actualiza tu calendario.",
           "cancelacion": "La sesión con el cliente se canceló. La invitación adjunta la quita de tu calendario.",
           "recordatorio": f"Recordatorio: faltan {dias} día{'s' if dias != 1 else ''} para la sesión con el cliente."}[tipo]
    if cc:
        enc += " Se copia a Dirección porque hay pendientes vencidos con el cliente: revísenlos con tiempo."
    cuerpo = _cuerpo(a, prep, cliente, enc)
    asunto = {"invitacion": "Sesión programada", "reprogramacion": "Sesión reprogramada", "cancelacion": "Sesión cancelada",
              "recordatorio": f"Recordatorio ({dias} días)"}[tipo] + f" · {cliente} · {a.fecha:%d/%m}"
    if tipo != "cancelacion":
        asunto = f"[{prep['flag'].upper()}] " + asunto
    metodo = "CANCEL" if tipo == "cancelacion" else "REQUEST"
    cal = None if tipo == "recordatorio" else (metodo, ics(a, metodo, _secuencia(a), para, descripcion_corta(a, prep), cliente,
                                                          prep["flag"]))
    try:
        estado = correo.enviar(para, asunto, cuerpo, f"{s.base_url}/clientes/agenda/{a.id}", boton="Abrir la sesión",
                               titulo="Agenda de clientes", cc=cc, calendario=cal,
                               pie="Comunicación interna de OneCore generada por la Bitácora MCI.")
    except Exception as e:  # noqa: BLE001 - el aviso falla, la agenda no
        log.warning("No se pudo enviar el aviso %s de la sesión %s: %s", tipo, a.id, e)
        estado = "error"
    _registrar(a, tipo, para, cc, prep["flag"], estado)
    evento(a, None, f"Aviso «{tipo}» por correo a {len(para)} persona(s) de OneCore"
           + (f" con copia a Dirección (bandera roja)" if cc else "") + ("" if estado != "error" else " · falló el envío") + ".")
    return {"estado": estado, "para": para, "cc": cc, "flag": prep["flag"]}


def enviar_en_fondo(aid: int, tipo: str) -> None:
    from ..db import SessionLocal
    with SessionLocal() as db:
        a = db.get(AgendaCliente, aid)
        if a:
            enviar(db, a, tipo)
            db.commit()


def recordatorios(db: Session) -> int:
    """Diario (L-V): sesiones a 5 días o menos que aún no tienen recordatorio para su fecha vigente.
    Las que se programaron con menos de 5 días ya llevaron la bandera en la invitación."""
    n = 0
    limite = hoy() + timedelta(days=DIAS_RECORDATORIO)
    for a in db.scalars(select(AgendaCliente).where(AgendaCliente.estado.in_(("programada", "reprogramada")),
                                                    AgendaCliente.fecha > hoy(), AgendaCliente.fecha <= limite)):
        if any(x["tipo"] == "recordatorio" and x.get("fecha") == a.fecha.isoformat() for x in a.avisos or []):
            continue
        ultima = max([datetime.fromisoformat(x["en"]).date() for x in a.avisos or [] if x["tipo"] in ("invitacion", "reprogramacion")]
                     or [(a.creado - timedelta(hours=6)).date()])
        if (a.fecha - ultima).days <= DIAS_RECORDATORIO:
            continue
        enviar(db, a, "recordatorio")
        n += 1
    db.commit()
    return n


def falta_transcripcion(db: Session) -> int:
    """Diario: cada sesión ya ejecutada sin transcripción le recuerda al responsable (con copia a Customer Success)."""
    from .clientes import evento
    s = get_settings()
    cs = sorted({x.email.lower() for x in db.scalars(select(Usuario).where(Usuario.activo.is_(True),
                                                                           Usuario.puesto == "customer_success")) if x.email})
    n = 0
    hoy_iso = hoy().isoformat()
    for a in db.scalars(select(AgendaCliente).where(AgendaCliente.estado.in_(("programada", "reprogramada")),
                                                    AgendaCliente.fecha < hoy(), AgendaCliente.sesion_id.is_(None))):
        if any(x["tipo"] == "falta_transcripcion" and x["en"][:10] == hoy_iso for x in a.avisos or []):
            continue
        para = [a.responsable.email] if a.responsable and a.responsable.activo and a.responsable.email else cs
        if not para:
            continue
        dias = (hoy() - a.fecha).days
        cliente = a.cliente_nombre or a.cliente_rfc or "Cliente"
        cuerpo = (f"La sesión con {cliente} del {a.fecha:%d/%m/%Y} se marcó como ejecutada sin transcripción "
                  f"(hace {dias} día{'s' if dias != 1 else ''}).\n\nLa transcripción es OBLIGATORIA: de ella sale la minuta "
                  f"(decisiones, acuerdos, riesgos y temas abiertos) y las quejas y tareas de seguimiento. Cárgala hoy en la "
                  f"bitácora.\n\nComunicación interna de OneCore.")
        try:
            estado = correo.enviar(para, f"Falta la transcripción · {cliente} · {a.fecha:%d/%m}", cuerpo,
                                   f"{s.base_url}/clientes/agenda/{a.id}", boton="Cargar la transcripción", titulo="Agenda de clientes",
                                   cc=[c for c in cs if c not in para], pie="Comunicación interna de OneCore generada por la Bitácora MCI.")
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar el recordatorio de transcripción de la sesión %s: %s", a.id, e)
            estado = "error"
        _registrar(a, "falta_transcripcion", para, [c for c in cs if c not in para], "", estado)
        evento(a, None, "Recordatorio diario: falta cargar la transcripción.")
        n += 1
    db.commit()
    return n


def flags(db: Session, sesiones: list[AgendaCliente]) -> dict[int, str]:
    """Bandera de preparación de las sesiones por venir (para la tabla de la agenda)."""
    return {a.id: preparacion(db, a)["flag"] for a in sesiones
            if a.estado in ("programada", "reprogramada") and a.fecha >= hoy()}


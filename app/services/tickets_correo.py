"""Correos de GuardIA Ticket: quién se entera de qué y con un texto claro de lo que se necesita.

Cada evento produce uno o varios avisos (destinatarios, asunto, cuerpo, botón). No se avisa a quien hizo la acción,
salvo la confirmación de ticket recibido a quien lo levantó.
Los usuarios entran con su correo, así que el destinatario es siempre Usuario.email.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import TICKET_CIERRES, TICKET_IMPACTOS, TICKET_TIPOS, Ticket, Usuario
from . import accesos, correo

log = logging.getLogger("bitacora.tickets.correo")
PIE = ("Para responder o adjuntar archivos usa el botón: las respuestas a este correo no llegan al ticket. "
       "GuardIA OneCore · Soporte")


def _hora(dt) -> str:
    return (dt - timedelta(hours=6)).strftime("%d/%m/%Y %H:%M") if dt else "—"


def _ok(u: Usuario | None, actor: Usuario | None) -> bool:
    return bool(u and u.activo and u.recibe_correos and u.email and (actor is None or u.id != actor.id))


def personas_triage(db: Session) -> list[Usuario]:
    us = db.scalars(select(Usuario).where(Usuario.activo.is_(True))).all()
    return [u for u in us if accesos.puede(u, "tickets-triage")]


def avisos(db: Session, evento: str, t: Ticket, actor: Usuario | None, **x) -> list[tuple[list[str], str, str, str]]:
    """Regresa [(correos, asunto, cuerpo, texto del botón)]."""
    out = []
    donde = " › ".join(p for p in (t.producto, t.menu, t.pantalla) if p)
    ficha = (f"Ticket: {t.folio} · {t.titulo}\nDónde: {donde or '—'}\n"
             f"Tipo: {TICKET_TIPOS.get(t.tipo, t.tipo)} · {TICKET_IMPACTOS.get(t.impacto, t.impacto)}"
             + (f"\nCliente: {t.cliente}" if t.cliente else ""))

    def a(usuarios, asunto, cuerpo, boton="Abrir el ticket", al_actor=False):
        correos = [u.email for u in usuarios if _ok(u, None if al_actor else actor)]
        if correos:
            out.append((correos, asunto, cuerpo, boton))

    if evento == "nuevo":
        falt = ""
        if t.faltantes:
            from .tickets import CAMPOS_REQ
            falt = ("\nSe levantó sin: " + ", ".join(CAMPOS_REQ.get(f, f).lower() for f in t.faltantes)
                    + ". Si lo consigues, agrégalo en el ticket: acelera la atención.")
        a([t.autor], f"{t.folio} recibido: {t.titulo}",
          f"Hola {t.autor.nombre.split()[0]}, recibimos tu ticket.\n{ficha}{falt}\n"
          "Qué sigue: el equipo de soporte le asigna prioridad y responsable; te avisaremos por este medio.", "Ver mi ticket",
          al_actor=True)
        a([u for u in personas_triage(db) if u.id != t.creado_por], f"Nuevo ticket por clasificar: {t.folio}",
          f"{t.autor.nombre} levantó un ticket.\n{ficha}\n"
          + ("GuardIA no pudo revisarlo al levantarlo." if t.validacion == "sin_ia" else
             ("Se levantó con datos faltantes." if t.validacion == "incompleto" else "GuardIA lo revisó y está completo.")),
          "Clasificar")
    elif evento == "triage":
        if t.asignado:
            a([t.asignado], f"{t.folio} ({t.prioridad}) te fue asignado",
              f"Te asignaron un ticket {t.prioridad}. Atender antes de: {_hora(t.sla_vence)}.\n{ficha}", "Atender el ticket")
        a([t.autor], f"{t.folio}: ya tiene prioridad {t.prioridad}",
          f"Tu ticket ya fue clasificado.\n{ficha}\nPrioridad: {t.prioridad}"
          + (f"\nResponsable: {t.asignado.nombre}" if t.asignado else "")
          + f"\nTiempo objetivo de atención: antes de {_hora(t.sla_vence)}.", "Ver mi ticket")
    elif evento == "en_curso":
        a([t.autor], f"{t.folio}: estamos trabajando en tu ticket",
          f"{(t.asignado or actor).nombre} ya está atendiendo tu ticket «{t.titulo}».\nTe avisaremos cuando esté resuelto.",
          "Ver mi ticket")
    elif evento == "pedir_info":
        a([t.autor], f"Acción requerida · {t.folio}: necesitamos información",
          f"Para seguir con tu ticket «{t.titulo}», {actor.nombre} necesita:\n- {x.get('pregunta', '').strip()}\n"
          "Responde en el ticket (puedes adjuntar archivos). Mientras no respondas, el ticket queda en pausa.",
          "Responder en el ticket")
    elif evento == "respuesta":
        a([t.asignado], f"{t.folio}: el usuario respondió",
          f"{actor.nombre} respondió en «{t.titulo}»:\n{x.get('texto', '')[:1500]}\nEl ticket regresó a atención.", "Ver respuesta")
    elif evento == "comentario":
        destino = [t.autor] if actor.id != t.creado_por else [t.asignado]
        a(destino, f"{t.folio}: nuevo comentario",
          f"{actor.nombre} comentó en «{t.titulo}»:\n{x.get('texto', '')[:1500]}", "Ver el ticket")
    elif evento == "cerrado":
        r = t.resolucion or {}
        a([t.autor], f"{t.folio} resuelto: {t.titulo}",
          f"Tu ticket quedó resuelto.\nCómo: {TICKET_CIERRES.get(r.get('tipo'), r.get('tipo', ''))}\n"
          f"Solución: {r.get('solucion', '')}\nSi vuelve a pasar, entra al ticket y usa «Reabrir».", "Ver la solución")
    elif evento == "reabierto":
        a([t.asignado], f"{t.folio} reabierto", f"{actor.nombre} reabrió «{t.titulo}»: {x.get('motivo', '')}", "Ver el ticket")
    return out


def enviar(db: Session, evento: str, t: Ticket, actor: Usuario | None, **x) -> int:
    """Envía los avisos del evento; un correo fallido no afecta al ticket. Regresa cuántos se enviaron."""
    enlace = f"{get_settings().base_url.rstrip('/')}/tickets/{t.id}"
    n = 0
    for correos, asunto, cuerpo, boton in avisos(db, evento, t, actor, **x):
        try:
            correo.enviar(correos, asunto, cuerpo, enlace, boton=boton, pie=PIE, titulo="Soporte")
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo avisar (%s) de %s a %s: %s", evento, t.folio, correos, e)
    return n


def en_fondo(evento: str, tid: int, actor_id: int | None, **x) -> None:
    from ..db import SessionLocal
    with SessionLocal() as db:
        t = db.get(Ticket, tid)
        if t:
            enviar(db, evento, t, db.get(Usuario, actor_id) if actor_id else None, **x)

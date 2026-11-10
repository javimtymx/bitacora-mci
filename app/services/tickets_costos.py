"""Horas, costos y pagos de GuardIA Ticket.

- Ejecutado: tickets cerrados en el periodo; costo = horas reales × tarifa del ingeniero guardada al cerrar.
- Pronóstico (presupuesto en curso): tickets planeados, en curso o en revisión; horas estimadas × tarifa actual.
- Pagos: un pago agrupa tickets cerrados de un ingeniero, con comprobante; un ticket pagado no se reabre.
Fechas en hora del centro de México (UTC-6), igual que el resto del sistema.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketPago, Usuario, utcnow
from .util import auditar

EN_CURSO = ("planeado", "en_curso", "revision")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")


class PagoInvalido(ValueError):
    pass


def _local(dt: datetime | None) -> date | None:
    return (dt - timedelta(hours=6)).date() if dt else None


def periodo(mes: str = "", desde: date | None = None, hasta: date | None = None, hoy: date | None = None):
    """Regresa (desde, hasta, etiqueta). Prioridad: rango explícito, luego mes AAAA-MM, luego el mes actual."""
    hoy = hoy or (utcnow() - timedelta(hours=6)).date()
    if desde and hasta:
        if hasta < desde:
            desde, hasta = hasta, desde
        return desde, hasta, f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
    try:
        a, m = (int(x) for x in mes.split("-"))
        assert 1 <= m <= 12 and 2000 < a < 2100
    except (ValueError, AssertionError):
        a, m = hoy.year, hoy.month
    d1 = date(a, m, 1)
    return d1, date(a, m, calendar.monthrange(a, m)[1]), f"{MESES[m - 1].capitalize()} {a}"


@dataclass
class Grupo:
    clave: str
    nombre: str
    n: int = 0
    horas: float = 0.0
    costo: float = 0.0
    pagado: float = 0.0
    sin_tarifa: int = 0
    tickets: list = field(default_factory=list)

    @property
    def por_pagar(self) -> float:
        return round(self.costo - self.pagado, 2)


def _sumar(g: Grupo, t: Ticket) -> None:
    g.n += 1
    g.horas += float(t.horas_reales or 0)
    c = t.costo
    if c is None:
        g.sin_tarifa += 1
    else:
        g.costo += c
        if t.pago_id:
            g.pagado += c
    g.tickets.append(t)


def _agrupar(ts: list[Ticket], llave) -> list[Grupo]:
    gs: dict[str, Grupo] = {}
    for t in ts:
        k, nombre = llave(t)
        _sumar(gs.setdefault(k, Grupo(k, nombre)), t)
    return sorted(gs.values(), key=lambda g: (-g.costo, -g.horas, g.nombre))


def cerrados(db: Session, desde: date, hasta: date) -> list[Ticket]:
    q = select(Ticket).where(Ticket.estado == "cerrado", Ticket.cerrado_en >= datetime.combine(desde, datetime.min.time()),
                             Ticket.cerrado_en < datetime.combine(hasta + timedelta(days=2), datetime.min.time()))
    return [t for t in db.scalars(q).all() if desde <= _local(t.cerrado_en) <= hasta]


def informe(db: Session, desde: date, hasta: date) -> dict:
    ts = cerrados(db, desde, hasta)
    total = Grupo("total", "Total")
    for t in ts:
        _sumar(total, t)
    abiertos = db.scalars(select(Ticket).where(Ticket.estado.in_(EN_CURSO)).order_by(Ticket.fecha_entrega)).all()
    pron: dict[str, Grupo] = {}
    pron_total = Grupo("total", "Total")
    for t in abiertos:
        k = str(t.asignado_id or 0)
        g = pron.setdefault(k, Grupo(k, t.asignado.nombre if t.asignado else "Sin ingeniero"))
        h = float(t.horas_estimadas or 0)
        tarifa = float(t.asignado.costo_hora) if t.asignado and t.asignado.costo_hora is not None else None
        for x in (g, pron_total):
            x.n += 1
            x.horas += h
            if tarifa is None:
                x.sin_tarifa += 1
            else:
                x.costo += h * tarifa
            x.tickets.append(t) if x is g else None
    en_periodo = [t for t in abiertos if t.fecha_entrega and desde <= t.fecha_entrega <= hasta]
    return {
        "total": total,
        "ingenieros": _agrupar(ts, lambda t: (str(t.asignado_id or 0), t.asignado.nombre if t.asignado else "Sin ingeniero")),
        "solicitantes": _agrupar(ts, lambda t: (str(t.creado_por), t.autor.nombre if t.autor else "—")),
        "modulos": _agrupar(ts, lambda t: ((t.producto or "—").upper(), t.producto or "—")),
        "tickets": sorted(ts, key=lambda t: t.cerrado_en, reverse=True),
        "pronostico": sorted(pron.values(), key=lambda g: -g.costo),
        "pronostico_total": pron_total,
        "entregas_periodo": len(en_periodo),
        "horas_entregas_periodo": sum(float(t.horas_estimadas or 0) for t in en_periodo),
    }


# ---------------------------------------------------------------- pagos

def por_pagar(db: Session, ingeniero_id: int | None = None) -> list[Grupo]:
    q = select(Ticket).where(Ticket.estado == "cerrado", Ticket.pago_id.is_(None), Ticket.asignado_id.is_not(None))
    if ingeniero_id:
        q = q.where(Ticket.asignado_id == ingeniero_id)
    ts = db.scalars(q.order_by(Ticket.cerrado_en)).all()
    return _agrupar(ts, lambda t: (str(t.asignado_id), t.asignado.nombre))


def pagos(db: Session, ingeniero_id: int | None = None, limite: int = 200) -> list[TicketPago]:
    q = select(TicketPago).order_by(TicketPago.fecha.desc(), TicketPago.id.desc())
    if ingeniero_id:
        q = q.where(TicketPago.ingeniero_id == ingeniero_id)
    return db.scalars(q.limit(limite)).all()


def _monto(txt) -> float:
    try:
        v = round(float(str(txt).replace(",", "").replace("$", "").strip()), 2)
    except ValueError as e:
        raise PagoInvalido("Escribe el monto en pesos, por ejemplo 12500.50.") from e
    if v <= 0:
        raise PagoInvalido("El monto debe ser mayor a cero.")
    return v


def registrar(db: Session, u: Usuario, *, ingeniero: Usuario, ticket_ids: list[int], fecha: date | None, monto: str,
              nota: str, comprobante: dict | None) -> TicketPago:
    """comprobante = {ruta, nombre, tipo, bytes} ya guardado en el almacén."""
    if not ticket_ids:
        raise PagoInvalido("Elige al menos un ticket.")
    if not comprobante:
        raise PagoInvalido("Adjunta el comprobante del pago.")
    ts = db.scalars(select(Ticket).where(Ticket.id.in_(ticket_ids))).all()
    if len(ts) != len(set(ticket_ids)):
        raise PagoInvalido("Algún ticket ya no existe.")
    for t in ts:
        if t.estado != "cerrado":
            raise PagoInvalido(f"{t.folio} no está cerrado.")
        if t.pago_id:
            raise PagoInvalido(f"{t.folio} ya está pagado.")
        if t.asignado_id != ingeniero.id:
            raise PagoInvalido(f"{t.folio} lo atendió otra persona.")
    p = TicketPago(ingeniero_id=ingeniero.id, fecha=fecha or (utcnow() - timedelta(hours=6)).date(),
                   monto=_monto(monto), nota=(nota or "").strip()[:2000], comprobante_ruta=comprobante["ruta"],
                   comprobante_nombre=comprobante["nombre"][:255], comprobante_tipo=comprobante.get("tipo", "")[:120],
                   comprobante_bytes=int(comprobante.get("bytes") or 0), registrado_por=u.id)
    db.add(p)
    db.flush()
    for t in ts:
        t.pago_id = p.id
    auditar(db, "ticket_pago", "ticket_pago", p.id, u.id, ingeniero=ingeniero.id, tickets=[t.id for t in ts],
            monto=float(p.monto))
    return p

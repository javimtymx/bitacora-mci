"""Carga de trabajo de soporte: cola por ingeniero, proyección en días hábiles, sugerencia de fecha y Gantt.

Regla: cada ingeniero dedica ~4 horas hábiles al día a tickets (se ajusta por persona en Usuarios). Su cola son los
tickets planeados o en curso asignados a él, en este orden:
- El orden manual que se fijó en la pantalla de cargas (arrastrando), si existe.
- Los que no tienen orden manual entran según prioridad (P1 antes que P2…), luego por fecha de entrega.
Cada ticket ocupa sus horas estimadas, día hábil tras día hábil (sin fines de semana ni festivos oficiales), desde hoy.
«No antes de» (inicio_plan) deja un ticket esperando hasta ese día aunque haya capacidad antes.
Nada de esto cambia fechas por sí solo: propone, y las fechas de entrega se actualizan solo si alguien lo confirma.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ticket, TicketComentario, TicketEstimacion, Usuario, utcnow
from .util import auditar

HORAS_DIA = 4.0
EN_COLA = ("planeado", "en_curso")
_PRIO = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def horas_dia(u: Usuario | None) -> float:
    try:
        h = float(u.horas_dia) if u is not None and u.horas_dia is not None else HORAS_DIA
    except (TypeError, ValueError, AttributeError):
        h = HORAS_DIA
    return h if 0 < h <= 12 else HORAS_DIA


def habil(d: date) -> bool:
    from .clientes import inhabil
    return not inhabil(d)


def siguiente_habil(d: date) -> date:
    while not habil(d):
        d += timedelta(days=1)
    return d


def dias_habiles(desde: date, n: int) -> list[date]:
    out, d = [], desde
    while len(out) < n:
        if habil(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _clave_auto(t: Ticket):
    return (0 if t.estado == "en_curso" else 1, _PRIO.get(t.prioridad or "", 4), t.fecha_entrega or date.max, t.id)


def ordenar(ts: list[Ticket]) -> list[Ticket]:
    """Orden manual donde exista; los que no lo tienen se insertan por prioridad antes del primero de menor prioridad."""
    manual = sorted([t for t in ts if t.orden_cola is not None], key=lambda t: (t.orden_cola, t.id))
    auto = sorted([t for t in ts if t.orden_cola is None], key=_clave_auto)
    out = list(manual)
    for t in auto:
        p = _PRIO.get(t.prioridad or "", 4)
        i = next((k for k, m in enumerate(out) if _PRIO.get(m.prioridad or "", 4) > p and m.estado != "en_curso"), len(out))
        out.insert(i, t)
    return out


def cola(db: Session, ingeniero_id: int, extra: Ticket | None = None) -> list[Ticket]:
    ts = list(db.scalars(select(Ticket).where(Ticket.asignado_id == ingeniero_id, Ticket.estado.in_(EN_COLA),
                                              Ticket.horas_estimadas.is_not(None))))
    if extra is not None:
        ts = [t for t in ts if t.id != extra.id] + [extra]
    return ordenar(ts)


@dataclass
class Tramo:
    ticket: Ticket
    horas: float
    inicio: date | None = None
    fin: date | None = None
    por_dia: dict = field(default_factory=dict)

    @property
    def tarde(self) -> bool:
        return bool(self.fin and self.ticket.fecha_entrega and self.fin > self.ticket.fecha_entrega)


def proyectar(ts: list[Ticket], capacidad: float, desde: date | None = None,
              horas: dict[int, float] | None = None) -> tuple[list[Tramo], dict[date, float]]:
    """Reparte las horas de cada ticket en días hábiles a `capacidad` horas por día. Regresa los tramos y la carga diaria."""
    dia = siguiente_habil(desde or date.today())
    libre = capacidad
    carga: dict[date, float] = {}
    tramos = []
    for t in ts:
        h = float((horas or {}).get(t.id, t.horas_estimadas or 0) or 0)
        tr = Tramo(t, h)
        if t.inicio_plan and t.inicio_plan > dia:
            dia, libre = siguiente_habil(t.inicio_plan), capacidad
        resta = h
        while resta > 1e-9:
            if libre <= 1e-9:
                dia, libre = siguiente_habil(dia + timedelta(days=1)), capacidad
                continue
            usa = min(libre, resta)
            tr.inicio = tr.inicio or dia
            tr.por_dia[dia] = tr.por_dia.get(dia, 0) + usa
            carga[dia] = carga.get(dia, 0) + usa
            resta -= usa
            libre -= usa
            tr.fin = dia
        if h == 0:
            tr.inicio = tr.fin = dia
        tramos.append(tr)
    return tramos, carga


def plan_ingeniero(db: Session, ing: Usuario, extra: Ticket | None = None, horas: dict[int, float] | None = None):
    return proyectar(cola(db, ing.id, extra), horas_dia(ing), horas=horas)


def sugerir(db: Session, t: Ticket, ing: Usuario, horas_est: float) -> dict:
    """Fecha sugerida para `t` con `ing` y qué tickets de su cola se recorrerían (comparado con la cola actual)."""
    antes, _ = plan_ingeniero(db, ing)
    fin_antes = {tr.ticket.id: tr.fin for tr in antes}
    sim = _Simulado(t, ing.id, horas_est)
    tramos, carga = proyectar(cola(db, ing.id, sim), horas_dia(ing), horas={t.id: horas_est})
    mio = next(tr for tr in tramos if tr.ticket.id == t.id)
    afectados = []
    for tr in tramos:
        x = tr.ticket
        if x.id == t.id:
            continue
        previo = fin_antes.get(x.id)
        if previo and tr.fin and tr.fin > previo:
            afectados.append({"id": x.id, "folio": x.folio, "titulo": x.titulo, "prioridad": x.prioridad or "",
                              "entrega": x.fecha_entrega.isoformat() if x.fecha_entrega else None,
                              "fin_antes": previo.isoformat(), "fin_nuevo": tr.fin.isoformat(),
                              "tarde": bool(x.fecha_entrega and tr.fin > x.fecha_entrega)})
    hoy = siguiente_habil(date.today())
    return {"ingeniero": ing.id, "nombre": ing.nombre, "capacidad": horas_dia(ing),
            "inicio": mio.inicio.isoformat() if mio.inicio else None, "fecha": mio.fin.isoformat() if mio.fin else None,
            "carga_hoy": round(sum(tr.por_dia.get(hoy, 0) for tr in antes), 1),
            "horas_en_cola": round(sum(tr.horas for tr in antes), 1), "afectados": afectados}


class _Simulado:
    """Ticket en planeación (aún sin guardar horas/ingeniero) para simular dónde caería en la cola."""

    def __init__(self, t: Ticket, ing_id: int, horas: float):
        self._t, self.asignado_id, self.horas_estimadas = t, ing_id, horas
        self.estado = t.estado if t.estado in EN_COLA else "planeado"
        self.orden_cola = t.orden_cola if t.asignado_id == ing_id else None

    def __getattr__(self, k):
        return getattr(self._t, k)


def disponibilidad(db: Session, t: Ticket, horas_est: float, gente: list[Usuario]) -> list[dict]:
    """Como «horarios disponibles» de un calendario: para cada ingeniero, cuándo podría entregarlo."""
    out = [sugerir(db, t, g, horas_est) for g in gente]
    return sorted(out, key=lambda x: (x["fecha"] or "9999", x["horas_en_cola"]))


# ---------------------------------------------------------------- cambios manuales (pantalla de cargas)

def fijar_orden(db: Session, u: Usuario, ing: Usuario, ids: list[int]) -> None:
    ts = {t.id: t for t in cola(db, ing.id)}
    k = 0
    for i in ids:
        if i in ts:
            k += 1
            ts[i].orden_cola = k
    for t in ts.values():
        if t.id not in ids:
            k += 1
            t.orden_cola = k
    auditar(db, "cargas_orden", "usuario", ing.id, u.id, orden=ids[:60])


def fijar_inicio(db: Session, u: Usuario, t: Ticket, inicio: date | None) -> None:
    if inicio and not habil(inicio):
        from .tickets import TicketInvalido
        raise TicketInvalido("Ese día no es hábil (fin de semana o festivo). Elige otro.")
    previo = t.inicio_plan
    t.inicio_plan = inicio
    db.add(TicketComentario(ticket_id=t.id, autor_id=u.id, tipo="sistema",
                            texto=(f"Programado para empezar no antes del {inicio:%d/%m/%Y}." if inicio
                                   else "Se quitó la fecha mínima de inicio.")
                                  + (f" (antes {previo:%d/%m/%Y})" if previo else "")))


def aplicar_fechas(db: Session, u: Usuario, cambios: dict[int, date], motivo: str) -> int:
    """Actualiza la fecha de entrega comprometida de los tickets que el usuario confirmó; queda en su historial."""
    from .tickets import TicketInvalido
    motivo = (motivo or "").strip()
    if cambios and len(motivo) < 5:
        raise TicketInvalido("Escribe el motivo del cambio de fechas (ej. «entró un P1 de SAFE»).")
    n = 0
    for tid, f in cambios.items():
        t = db.get(Ticket, tid)
        if not t or t.estado not in EN_COLA or t.fecha_entrega == f:
            continue
        previa = t.fecha_entrega
        t.fecha_entrega, t.actualizado = f, utcnow()
        db.add(TicketEstimacion(ticket=t, version=len(t.estimaciones) + 1, horas=t.horas_estimadas, fecha_entrega=f,
                                asignado_id=t.asignado_id, motivo=f"Reprogramado por carga: {motivo}"[:1000], por=u.id))
        db.add(TicketComentario(ticket_id=t.id, autor_id=u.id, tipo="sistema",
                                texto=f"Fecha de entrega {previa:%d/%m/%Y} → {f:%d/%m/%Y} por carga del ingeniero. Motivo: {motivo}"
                                if previa else f"Fecha de entrega {f:%d/%m/%Y} por carga del ingeniero. Motivo: {motivo}"))
        n += 1
    return n


# ---------------------------------------------------------------- vistas

def gantt(db: Session, gente: list[Usuario], dias: int = 30) -> dict:
    """Filas por ingeniero con sus barras proyectadas, en días hábiles desde hoy."""
    eje = dias_habiles(date.today(), dias)
    idx = {d: i for i, d in enumerate(eje)}
    filas = []
    for g in gente:
        tramos, carga = plan_ingeniero(db, g)
        if not tramos:
            continue
        barras = []
        for tr in tramos:
            if not tr.inicio:
                continue
            i0 = idx.get(tr.inicio)
            if i0 is None:
                continue
            i1 = idx.get(tr.fin, len(eje) - 1)
            e = tr.ticket.fecha_entrega
            ie = idx.get(e) if e else None
            if e and ie is None and e < eje[0]:
                ie = -1
            barras.append({"t": tr.ticket, "i0": i0, "i1": i1, "tarde": tr.tarde, "entrega_i": ie, "horas": tr.horas,
                           "corta": tr.fin not in idx})
        filas.append({"ing": g, "capacidad": horas_dia(g), "barras": barras,
                      "carga": [round(carga.get(d, 0), 1) for d in eje]})
    return {"eje": eje, "filas": filas}


def cargas(db: Session, ing: Usuario, dias: int = 15) -> dict:
    eje = dias_habiles(date.today(), dias)
    tramos, carga = plan_ingeniero(db, ing)
    return {"ing": ing, "capacidad": horas_dia(ing), "eje": eje, "tramos": tramos,
            "carga": [(d, round(carga.get(d, 0), 1)) for d in eje],
            "tarde": [tr for tr in tramos if tr.tarde]}

"""Cálculo de seguimiento: cumplimiento con excepciones, estado por persona y alertas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Compromiso,
    Estrategia,
    Excepcion,
    Idea,
    RevisionIA,
    Usuario,
)
from . import parametros
from .util import dias_desde, hoy, semana_iso, semanas_atras


def excepciones_aprobadas(db: Session) -> list[Excepcion]:
    return list(db.scalars(select(Excepcion).where(Excepcion.estado == "aprobada")))


def esta_exceptuado(c: Compromiso, excs: list[Excepcion]) -> Excepcion | None:
    for e in excs:
        if e.efecto != "excluir":
            continue
        if e.tipo_entidad == "compromiso" and e.entidad_id == c.id:
            return e
        if e.tipo_entidad == "persona" and e.entidad_id == c.responsable_id and e.periodo_desde <= c.fecha_limite <= e.periodo_hasta:
            return e
        if e.tipo_entidad == "predictiva" and e.entidad_id == c.predictiva_id and e.periodo_desde <= c.fecha_limite <= e.periodo_hasta:
            return e
    return None


@dataclass
class Cumplimiento:
    cumplidos: int = 0
    no_cumplidos: int = 0
    exceptuados: int = 0

    @property
    def cerrados(self) -> int:
        return self.cumplidos + self.no_cumplidos

    @property
    def pct(self) -> float | None:
        return self.cumplidos / self.cerrados * 100 if self.cerrados else None


def cumplimiento(compromisos: list[Compromiso], excs: list[Excepcion]) -> Cumplimiento:
    r = Cumplimiento()
    for c in compromisos:
        if c.estado not in ("cumplido", "no_cumplido"):
            continue
        if c.estado == "no_cumplido" and esta_exceptuado(c, excs):
            r.exceptuados += 1
            continue
        if c.estado == "cumplido":
            r.cumplidos += 1
        else:
            r.no_cumplidos += 1
    return r


def vencido(c: Compromiso) -> bool:
    return c.estado == "abierto" and c.fecha_limite < hoy()


def sin_remediacion(c: Compromiso) -> bool:
    return c.estado == "no_cumplido" and c.remediacion is None


def remediacion_vencida(c: Compromiso) -> bool:
    r = c.remediacion
    return c.estado == "no_cumplido" and r is not None and r.estado == "abierta" and r.nueva_fecha < hoy()


def avance_bajo(c: Compromiso) -> bool:
    if c.estado != "abierto":
        return False
    ini = c.creado.date()
    fin = c.fecha_limite
    total = (fin - ini).days
    if total <= 0:
        return False
    frac = min(1.0, max(0.0, (hoy() - ini).days / total))
    return frac > 0.4 and c.avance < frac * 100 - 25


@dataclass
class EstadoPersona:
    usuario: Usuario
    semana: int = 0
    cumpl: Cumplimiento = field(default_factory=Cumplimiento)
    vencidos: int = 0
    sin_remediar: int = 0
    avance_bajo: int = 0
    ultima_captura: datetime | None = None
    ideas_mes: int = 0
    ideas_debiles: int = 0
    estado: str = "ok"  # ok | revisar | atender

    @property
    def dias_sin_captura(self) -> int | None:
        return dias_desde(self.ultima_captura)


def estado_personas(db: Session, area_id: int | None = None) -> list[EstadoPersona]:
    q = select(Usuario).where(Usuario.activo.is_(True))
    if area_id:
        q = q.where(Usuario.area_id == area_id)
    usuarios = list(db.scalars(q.order_by(Usuario.nombre)))
    w4 = set(semanas_atras(4))
    sem = semana_iso(hoy())
    excs = excepciones_aprobadas(db)
    comps = list(db.scalars(select(Compromiso).where(Compromiso.semana.in_(w4 | {sem}) | (Compromiso.estado == "abierto") | (Compromiso.estado == "no_cumplido"))))
    hace31 = datetime.combine(hoy() - timedelta(days=31), datetime.min.time())
    ideas = list(db.scalars(select(Idea).where(Idea.creado >= hace31)))
    rev = parametros.get(db, "dias_sin_captura_revisar")
    ate = parametros.get(db, "dias_sin_captura_atender")
    out = []
    wd = hoy().weekday()
    for u in usuarios:
        mine = [c for c in comps if c.responsable_id == u.id]
        e = EstadoPersona(usuario=u)
        e.semana = sum(1 for c in mine if c.semana == sem)
        e.cumpl = cumplimiento([c for c in mine if c.semana in w4], excs)
        e.vencidos = sum(1 for c in mine if vencido(c) and not esta_exceptuado(c, excs))
        e.sin_remediar = sum(1 for c in mine if (sin_remediacion(c) or remediacion_vencida(c)) and not esta_exceptuado(c, excs))
        e.avance_bajo = sum(1 for c in mine if avance_bajo(c))
        fechas = [c.actualizado for c in mine] + [c.creado for c in mine]
        fechas += [n.creado for c in mine for n in c.notas if n.usuario_id == u.id]
        mis_ideas = [i for i in ideas if i.autor_id == u.id]
        fechas += [i.actualizado for i in mis_ideas]
        e.ultima_captura = max(fechas) if fechas else None
        e.ideas_mes = len(mis_ideas)
        e.ideas_debiles = sum(1 for i in mis_ideas if i.fortaleza == "debil" and i.estado != "descartada")
        d = e.dias_sin_captura
        if e.vencidos or e.sin_remediar or (e.semana == 0 and wd >= 1) or d is None or d >= ate:
            e.estado = "atender"
        elif (d is not None and d >= rev) or e.avance_bajo or e.ideas_debiles:
            e.estado = "revisar"
        out.append(e)
    orden = {"atender": 0, "revisar": 1, "ok": 2}
    return sorted(out, key=lambda x: (orden[x.estado], x.usuario.nombre))


@dataclass
class Alerta:
    severidad: int  # 3 urgente, 2 atender, 1 revisar
    texto: str
    usuario: Usuario | None = None
    area_nombre: str = ""
    compromiso_id: int | None = None
    estrategia_id: int | None = None


def alertas(db: Session, area_id: int | None = None) -> list[Alerta]:
    out: list[Alerta] = []
    excs = excepciones_aprobadas(db)
    sem = semana_iso(hoy())
    for e in estado_personas(db, area_id):
        u = e.usuario
        an = u.area.nombre if u.area else ""
        if e.semana == 0 and hoy().weekday() >= 1:
            out.append(Alerta(2, f"Sin compromiso registrado para la semana {sem[-2:]}.", u, an))
        d = e.dias_sin_captura
        if d is None:
            out.append(Alerta(2, "No ha registrado nada en la bitácora.", u, an))
        elif d >= parametros.get(db, "dias_sin_captura_atender"):
            out.append(Alerta(2, f"Sin actualizar desde hace {d} días.", u, an))
        elif d >= parametros.get(db, "dias_sin_captura_revisar"):
            out.append(Alerta(1, f"Sin actualizar desde hace {d} días.", u, an))
        if e.ideas_debiles:
            out.append(Alerta(1, f"{e.ideas_debiles} idea(s) calificadas como débiles por fortalecer.", u, an))
    q = select(Compromiso).where(Compromiso.estado.in_(("abierto", "no_cumplido")))
    if area_id:
        q = q.where(Compromiso.area_id == area_id)
    for c in db.scalars(q):
        if esta_exceptuado(c, excs):
            continue
        t = f"«{c.texto[:90]}»"
        base = dict(usuario=c.responsable, area_nombre=c.area.nombre, compromiso_id=c.id)
        if vencido(c):
            out.append(Alerta(3, f"Compromiso vencido el {c.fecha_limite:%d/%m} sin cerrar: {t}", **base))
        elif avance_bajo(c):
            out.append(Alerta(1, f"Avance de {c.avance}% con buena parte del plazo transcurrido: {t}", **base))
        if sin_remediacion(c):
            out.append(Alerta(3, f"No cumplido sin remediación: {t}", **base))
        if remediacion_vencida(c):
            out.append(Alerta(3, f"Remediación vencida el {c.remediacion.nueva_fecha:%d/%m}: {t}", **base))
    limite = parametros.get(db, "dias_estrategia_sin_actividad")
    qe = select(Estrategia).where(Estrategia.estado == "activa")
    if area_id:
        qe = qe.where(Estrategia.area_id == area_id)
    for est in db.scalars(qe):
        ultimo = db.scalar(select(Compromiso.creado).where(Compromiso.estrategia_id == est.id)
                           .order_by(Compromiso.creado.desc()).limit(1))
        d = dias_desde(ultimo or est.inicio)
        if d is not None and d >= limite:
            out.append(Alerta(2, f"Estrategia activa sin compromisos ligados en {d} días: «{est.nombre}».",
                              est.responsable, est.area.nombre, estrategia_id=est.id))
    return sorted(out, key=lambda a: -a.severidad)


def aceptacion_ia(db: Session, desde: date | None = None) -> tuple[int, int, float | None]:
    q = select(RevisionIA).where(RevisionIA.aceptada.is_not(None))
    if desde:
        q = q.where(RevisionIA.creado >= datetime.combine(desde, datetime.min.time()))
    revs = list(db.scalars(q))
    a = sum(1 for r in revs if r.aceptada)
    return a, len(revs), (a / len(revs) * 100 if revs else None)


def stats_estrategia(db: Session, est: Estrategia) -> dict:
    comps = list(db.scalars(select(Compromiso).where(Compromiso.estrategia_id == est.id).order_by(Compromiso.creado)))
    excs = excepciones_aprobadas(db)
    cum = cumplimiento(comps, excs)
    causas: dict[str, int] = {}
    rem_ok = rem_mal = 0
    for c in comps:
        for r in c.remediaciones:
            causas[r.causa_categoria] = causas.get(r.causa_categoria, 0) + 1
            if r.estado == "cumplida":
                rem_ok += 1
            elif r.estado == "no_cumplida":
                rem_mal += 1
    por_semana: dict[str, list[int]] = {}
    for c in comps:
        if c.estado in ("cumplido", "no_cumplido"):
            por_semana.setdefault(c.semana, [0, 0])
            por_semana[c.semana][0 if c.estado == "cumplido" else 1] += 1
    return {"compromisos": len(comps), "cumplidos": cum.cumplidos, "no_cumplidos": cum.no_cumplidos,
            "exceptuados": cum.exceptuados, "pct": cum.pct, "causas": causas,
            "remediaciones_cumplidas": rem_ok, "remediaciones_fallidas": rem_mal,
            "por_semana": dict(sorted(por_semana.items())), "lista": comps}

"""Indicadores semanales: valor real contra objetivo, brecha, racha y compromisos que los mueven.

El objetivo de cada semana sale de la versión de la MCI vigente ese viernes:
- tipo_meta "nivel": la meta se compara directo (retención 95%).
- tipo_meta "acumulado": ritmo lineal de linea_base (en vigente_desde) a meta (en fecha_meta).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Area, Compromiso, Estrategia, EstrategiaEvento, Excepcion, Mci, MciVersion, Medicion, Usuario
from . import catalogo, seguimiento
from .util import hoy, semana_iso


def viernes_de(semana: str) -> date:
    y, w = semana.split("-W")
    return date.fromisocalendar(int(y), int(w), 5)


def semanas(n: int, hasta: str | None = None) -> list[str]:
    fin = viernes_de(hasta) if hasta else hoy()
    return [semana_iso(fin - timedelta(weeks=i)) for i in range(n - 1, -1, -1)]


def objetivo(v: MciVersion | None, dia: date) -> float | None:
    if v is None or v.meta is None:
        return None
    meta = float(v.meta)
    if v.tipo_meta != "acumulado" or not v.fecha_meta or v.linea_base is None:
        return meta
    base = float(v.linea_base)
    total = (v.fecha_meta - v.vigente_desde).days or 1
    t = min(max((dia - v.vigente_desde).days / total, 0.0), 1.0)
    return base + (meta - base) * t


@dataclass
class PuntoSemana:
    semana: str
    valor: float | None = None
    objetivo: float | None = None
    brecha: float | None = None  # positiva = ganando, ya ajustada por el sentido de la meta
    cumplidos: int = 0
    remediados: int = 0
    en_remediacion: int = 0
    solo_texto: int = 0
    exceptuados: int = 0
    abiertos: int = 0

    @property
    def compromisos(self) -> int:
        return self.cumplidos + self.remediados + self.en_remediacion + self.solo_texto

    @property
    def pct_cumplimiento(self) -> float | None:
        return self.cumplidos / self.compromisos * 100 if self.compromisos else None


@dataclass
class SerieMci:
    mci: Mci
    version: MciVersion | None
    puntos: list[PuntoSemana] = field(default_factory=list)

    @property
    def ultimo(self) -> PuntoSemana | None:
        for p in reversed(self.puntos):
            if p.valor is not None:
                return p
        return None

    @property
    def racha(self) -> tuple[str, int]:
        """('ganando'|'perdiendo'|'en_meta'|'sin_datos', semanas seguidas)."""
        vals = [p for p in self.puntos if p.brecha is not None]
        if not vals:
            return ("sin_meta" if any(p.valor is not None for p in self.puntos) else "sin_datos"), 0
        def signo(b):
            return "ganando" if b > 1e-9 else ("perdiendo" if b < -1e-9 else "en_meta")
        s = signo(vals[-1].brecha)
        n = 0
        for p in reversed(vals):
            if signo(p.brecha) != s:
                break
            n += 1
        return s, n


def clasificar(c: Compromiso, excs) -> str | None:
    """cumplido | remediado | en_remediacion | solo_texto | exceptuado | None (abierto o cancelado)."""
    if c.estado == "cumplido":
        return "cumplido"
    if c.estado != "no_cumplido":
        return None
    if seguimiento.esta_exceptuado(c, excs):
        return "exceptuado"
    r = c.remediacion
    if r is None:
        return "solo_texto"
    if r.estado == "cumplida":
        return "remediado"
    if r.estado == "abierta" and r.nueva_fecha >= hoy():
        return "en_remediacion"
    return "solo_texto"


def serie_mci(db: Session, mci: Mci, n: int = 16, hasta: str | None = None) -> SerieMci:
    sems = semanas(n, hasta)
    ver_ids = [v.id for v in mci.versiones]
    meds = {}
    if ver_ids:
        for m in db.scalars(select(Medicion).where(Medicion.tipo == "mci", Medicion.version_id.in_(ver_ids),
                                                   Medicion.periodo.in_(sems))):
            meds[m.periodo] = float(m.valor)
    pred_ids = [p.id for p in mci.predictivas]
    comps = list(db.scalars(select(Compromiso).where(Compromiso.predictiva_id.in_(pred_ids),
                                                     Compromiso.semana.in_(sems)))) if pred_ids else []
    excs = seguimiento.excepciones_aprobadas(db)
    out = SerieMci(mci=mci, version=catalogo.version_vigente(mci.versiones))
    for s in sems:
        dia = viernes_de(s)
        v = catalogo.version_vigente(mci.versiones, dia)
        p = PuntoSemana(semana=s, valor=meds.get(s), objetivo=objetivo(v, dia))
        if p.valor is not None and p.objetivo is not None:
            signo = -1 if (v and v.sentido == "menor") else 1
            p.brecha = (p.valor - p.objetivo) * signo
        for c in comps:
            if c.semana != s:
                continue
            k = clasificar(c, excs)
            if k == "cumplido":
                p.cumplidos += 1
            elif k == "remediado":
                p.remediados += 1
            elif k == "en_remediacion":
                p.en_remediacion += 1
            elif k == "solo_texto":
                p.solo_texto += 1
            elif k == "exceptuado":
                p.exceptuados += 1
            elif c.estado == "abierto":
                p.abiertos += 1
        out.puntos.append(p)
    return out


def eventos_mci(db: Session, mci: Mci, sems: list[str]) -> list[tuple[str, str]]:
    """Eventos que explican cambios: estrategias, versiones de meta y excepciones."""
    ini, fin = viernes_de(sems[0]) - timedelta(days=4), viernes_de(sems[-1]) + timedelta(days=2)
    ev: list[tuple[str, str]] = []
    for v in mci.versiones:
        if ini <= v.vigente_desde <= fin and v.version > 1:
            ev.append((semana_iso(v.vigente_desde), f"Meta v{v.version}: {v.meta} {v.unidad} — {v.motivo_cambio or ''}"))
    ests = [e.id for e in db.scalars(select(Estrategia).where(Estrategia.mci_id == mci.id))]
    if ests:
        for e in db.scalars(select(EstrategiaEvento).where(EstrategiaEvento.estrategia_id.in_(ests),
                                                           EstrategiaEvento.tipo.in_(("cambio_estado", "ajuste")))):
            d = e.creado.date()
            if ini <= d <= fin:
                t = f"{e.estado_nuevo}: {e.estrategia.nombre}" if e.tipo == "cambio_estado" else f"Ajuste: {e.estrategia.nombre}"
                ev.append((semana_iso(d), t))
    pred_ids = [p.id for p in mci.predictivas]
    for x in db.scalars(select(Excepcion).where(Excepcion.estado == "aprobada")):
        if (x.tipo_entidad == "mci" and x.entidad_id == mci.id) or (x.tipo_entidad == "predictiva" and x.entidad_id in pred_ids):
            if ini <= x.periodo_desde <= fin:
                ev.append((semana_iso(x.periodo_desde), f"Excepción: {x.justificacion[:80]}"))
    return sorted(ev)


def mcis_de(db: Session, area_id: int | None = None) -> list[Mci]:
    q = select(Mci).where(Mci.activo.is_(True))
    if area_id:
        q = q.where(Mci.area_id == area_id)
    return list(db.scalars(q.order_by(Mci.area_id, Mci.id)))


def semanas_persona(db: Session, usuario_ids: list[int], sems: list[str]) -> dict[int, list[str]]:
    """Por persona y semana: g cumplió todo · r algo no cumplido · x exceptuado · a abierto · n sin compromiso."""
    excs = seguimiento.excepciones_aprobadas(db)
    comps = list(db.scalars(select(Compromiso).where(Compromiso.responsable_id.in_(usuario_ids),
                                                     Compromiso.semana.in_(sems)))) if usuario_ids else []
    out = {}
    for uid in usuario_ids:
        fila = []
        for s in sems:
            cs = [c for c in comps if c.responsable_id == uid and c.semana == s and c.estado != "cancelado"]
            if not cs:
                fila.append("n")
            elif any(c.estado == "no_cumplido" and not seguimiento.esta_exceptuado(c, excs) for c in cs):
                fila.append("r")
            elif any(c.estado == "abierto" for c in cs):
                fila.append("a")
            elif all(c.estado == "cumplido" for c in cs):
                fila.append("g")
            else:
                fila.append("x")
        out[uid] = fila
    return out


def fmt(v: float | None, dec: int = 1) -> str:
    if v is None:
        return "—"
    txt = f"{v:,.{dec}f}"
    return txt.rstrip("0").rstrip(".") if "." in txt else txt


def decimales(mci: Mci) -> int:
    """Decimales para mostrar: hasta 3 (99.968%); 1 en crecimientos calculados de CFDI."""
    v = catalogo.version_vigente(mci.versiones)
    return 1 if v and v.fuente in ("cfdi_recurrente", "cfdi_total") else 3


# ---------------------------------------------------------------- medidas predictivas (palancas)

@dataclass
class CeldaPredictiva:
    semana: str
    valor: float | None = None
    meta: float | None = None
    sentido: str = "mayor"
    cumplidos: int = 0
    compromisos: int = 0
    lista: list = field(default_factory=list)  # compromisos de esa predictiva en esa semana

    @property
    def falta(self) -> float | None:
        if self.meta is None or self.sentido == "menor":
            return None
        return max(0.0, self.meta - (self.valor or 0.0))

    @property
    def estado(self) -> str:
        """g en meta · r fuera · n capturado sin meta · '' sin captura."""
        if self.valor is None:
            return ""
        if self.meta is None:
            return "n"
        ok = self.valor <= self.meta if self.sentido == "menor" else self.valor >= self.meta
        return "g" if ok else "r"


def mediciones_predictivas(db: Session, preds, sems: list[str]) -> dict[tuple[int, str], float]:
    ver = {v.id: p.id for p in preds for v in p.versiones}
    if not ver:
        return {}
    return {(ver[m.version_id], m.periodo): float(m.valor)
            for m in db.scalars(select(Medicion).where(Medicion.tipo == "predictiva", Medicion.version_id.in_(list(ver)),
                                                       Medicion.periodo.in_(sems)))}


def matriz_predictivas(db: Session, preds, sems: list[str], comps) -> list[tuple]:
    """Por predictiva y semana: valor capturado contra meta semanal y compromisos cumplidos / registrados."""
    meds = mediciones_predictivas(db, preds, sems)
    out = []
    for p in preds:
        fila = []
        for s in sems:
            v = catalogo.version_vigente(p.versiones, viernes_de(s)) if p.versiones else None
            cs = [c for c in comps if c.predictiva_id == p.id and c.semana == s and c.estado != "cancelado"]
            fila.append(CeldaPredictiva(semana=s, valor=meds.get((p.id, s)),
                                        meta=float(v.meta_semanal) if v and v.meta_semanal is not None else None,
                                        sentido=v.sentido if v else "mayor",
                                        cumplidos=sum(1 for c in cs if c.estado == "cumplido"), compromisos=len(cs),
                                        lista=sorted(cs, key=lambda c: (c.estado != "abierto", c.fecha_limite))))
        vig = catalogo.version_vigente(p.versiones) if p.versiones else None
        out.append((p, vig, fila))
    return out

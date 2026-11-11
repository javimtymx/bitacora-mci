"""Reporte de rendición de cuentas (PDF 16:9): vista general por área y, por área, sus compromisos agrupados por
medida predictiva, con lo no cumplido primero, luego lo abierto y al final lo cumplido; texto completo, fechas y notas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Area, Compromiso, Mci, Predictiva
from . import indicadores, seguimiento
from .util import hoy, semana_iso

# orden de los bloques dentro de cada medida predictiva
BLOQUE = {"no_cumplido": 0, "en_remediacion": 0, "exceptuado": 0, "remediado": 0, "vencido": 1, "abierto": 1, "cumplido": 2}


@dataclass
class Estado:
    clave: str
    texto: str
    color: str  # g r o b n
    icono: str


def estado(c: Compromiso, excs) -> Estado:
    if c.estado == "cumplido":
        return Estado("cumplido", "Cumplido", "g", "✓")
    if c.estado == "no_cumplido":
        k = indicadores.clasificar(c, excs)
        if k == "exceptuado":
            return Estado("exceptuado", "No cumplido · exceptuado", "n", "✗")
        if k == "remediado":
            return Estado("remediado", "No cumplido · remediado", "o", "✗")
        if k == "en_remediacion":
            return Estado("en_remediacion", "No cumplido · en remediación", "r", "✗")
        if c.remediacion is None:
            return Estado("no_cumplido", "No cumplido · sin remediación", "r", "✗")
        return Estado("no_cumplido", "No cumplido · remediación vencida", "r", "✗")
    if c.fecha_limite < hoy():
        return Estado("vencido", "Abierto · vencido", "r", "○")
    return Estado("abierto", "Abierto", "b", "○")


def periodo(tipo: str, semana: str | None, mes: str | None, desde: date | None, hasta: date | None):
    """(semanas, etiqueta, mes de referencia). tipo: semana | mes | rango."""
    if tipo == "rango" and desde and hasta:
        if hasta < desde:
            desde, hasta = hasta, desde
        sems, d = [], desde
        while d <= hasta:
            s = semana_iso(d)
            if s not in sems and desde <= indicadores.viernes_de(s) <= hasta + timedelta(days=6):
                sems.append(s)
            d += timedelta(days=1)
        sems = sems or [semana_iso(hasta)]
        return sems, f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}", indicadores.mes_de(sems[-1])
    if tipo == "mes":
        m = indicadores.mes_valido(mes)
        sems = indicadores.semanas_mes(m) or [indicadores.semana_de_mes(m)]
        return sems, indicadores.nombre_mes(m).capitalize(), m
    sem = semana or semana_iso(hoy())
    return [sem], f"Semana {int(sem[-2:])} · {indicadores.nombre_mes(indicadores.mes_de(sem))}", indicadores.mes_de(sem)


@dataclass
class GrupoPred:
    predictiva: Predictiva | None
    mci: Mci | None
    version: object
    celdas: list  # CeldaPredictiva por semana del periodo
    comps: list[Compromiso] = field(default_factory=list)

    @property
    def cumplidos(self) -> int:
        return sum(1 for c in self.comps if c.estado == "cumplido")


@dataclass
class MciMes:
    mci: Mci
    version: object
    mes: object  # PuntoMes
    racha: tuple
    dec: int


@dataclass
class AreaReporte:
    area: Area
    mcis: list[MciMes]
    grupos: list[GrupoPred]
    estados: dict[int, Estado]

    @property
    def comps(self) -> list[Compromiso]:
        return [c for g in self.grupos for c in g.comps]

    def cuenta(self, *claves: str) -> int:
        return sum(1 for c in self.comps if self.estados[c.id].clave in claves)

    @property
    def cumplidos(self) -> int:
        return self.cuenta("cumplido")

    @property
    def no_cumplidos(self) -> int:
        return self.cuenta("no_cumplido", "en_remediacion", "remediado")

    @property
    def sin_remediacion(self) -> int:
        return sum(1 for c in self.comps if c.estado == "no_cumplido" and c.remediacion is None)

    @property
    def abiertos(self) -> int:
        return self.cuenta("abierto", "vencido")

    @property
    def vencidos(self) -> int:
        return self.cuenta("vencido")

    @property
    def pct(self) -> float | None:
        cerrados = self.cumplidos + self.no_cumplidos
        return self.cumplidos / cerrados * 100 if cerrados else None

    @property
    def veredicto(self) -> str:
        rs = [m.racha[0] for m in self.mcis]
        if "perdiendo" in rs:
            return "perdiendo"
        if rs and all(r in ("ganando", "en_meta") for r in rs):
            return "ganando"
        if any(r in ("en_curso", "ganando", "en_meta") for r in rs):
            return "en_curso"
        return "sin_datos"


def area_reporte(db: Session, area: Area, sems: list[str]) -> AreaReporte:
    excs = seguimiento.excepciones_aprobadas(db)
    preds = list(db.scalars(select(Predictiva).join(Mci).where(Mci.area_id == area.id, Mci.activo.is_(True))
                            .order_by(Mci.id, Predictiva.id)))
    comps = [c for c in db.scalars(select(Compromiso).where(Compromiso.area_id == area.id, Compromiso.semana.in_(sems),
                                                            Compromiso.estado != "cancelado"))]
    estados = {c.id: estado(c, excs) for c in comps}
    matriz = {p.id: (v, fila) for p, v, fila in indicadores.matriz_predictivas(db, preds, sems, comps)}
    orden = lambda c: (BLOQUE.get(estados[c.id].clave, 1), c.fecha_limite, c.id)
    grupos = []
    for p in preds:
        cs = sorted((c for c in comps if c.predictiva_id == p.id), key=orden)
        if not cs and not p.activo:
            continue
        v, fila = matriz.get(p.id, (None, []))
        grupos.append(GrupoPred(predictiva=p, mci=p.mci, version=v, celdas=fila, comps=cs))
    sueltos = sorted((c for c in comps if c.predictiva_id is None or c.predictiva_id not in matriz), key=orden)
    if sueltos:
        grupos.append(GrupoPred(predictiva=None, mci=None, version=None, celdas=[], comps=sueltos))
    mcis = []
    for m in indicadores.mcis_de(db, area.id):
        sr = indicadores.serie_mci(db, m, 1, sems[-1], n_meses=1)
        mcis.append(MciMes(mci=m, version=sr.version, mes=sr.actual, racha=sr.racha, dec=indicadores.decimales(m)))
    return AreaReporte(area=area, mcis=mcis, grupos=grupos, estados=estados)

"""Indicadores de Ventas calculados desde los CFDI de Panel AYF.

- cfdi_recurrente: facturación mensual de los clientes recurrentes activos (MRR facturado).
  Para la semana que cierra en viernes F, cada cliente aporta lo facturado en el mes de F; si aún no se le
  factura ese mes, lo del mes anterior. Así la medición no cae a cero al iniciar cada mes.
- cfdi_total: facturación total del último mes cerrado.

El valor que se guarda (y que ve todo el equipo) es el % de crecimiento contra la base. Los montos en pesos
solo se muestran a quien tiene permiso de datos confidenciales; a los demás se les muestra el crecimiento en
dinero que falta por mes para llegar a la meta."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClienteEstatus, Mci, MciVersion, Medicion, VentaClienteMes
from . import catalogo, indicadores
from .util import hoy, semana_iso

FUENTES = {"captura": "Captura manual", "cfdi_recurrente": "CFDI · recurrente (Panel AYF)",
           "cfdi_total": "CFDI · facturación total (Panel AYF)"}


def _mes(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _mes_anterior(per: str) -> str:
    y, m = map(int, per.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def _meses_entre(a: str, b: str) -> list[str]:
    out, cur = [], a
    while cur <= b and len(out) < 120:
        out.append(cur)
        y, m = map(int, cur.split("-"))
        cur = f"{y + (m == 12)}-{m % 12 + 1:02d}"
    return out


class Datos:
    """Carga una sola vez las ventas por cliente y el estatus de clientes."""

    def __init__(self, db: Session):
        self.por_cli: dict[tuple[str, str], float] = {}
        self.por_mes: dict[str, float] = {}
        for v in db.scalars(select(VentaClienteMes)):
            t = float(v.total_mxn)
            self.por_cli[(v.periodo, v.rfc)] = t
            self.por_mes[v.periodo] = self.por_mes.get(v.periodo, 0.0) + t
        self.estatus = {c.rfc: c for c in db.scalars(select(ClienteEstatus))}
        self.meses = sorted(self.por_mes)

    def recurrentes(self, dia: date | None = None) -> list[str]:
        out = []
        for rfc, c in self.estatus.items():
            if not c.recurrente:
                continue
            if dia and c.baja and (c.fecha_baja is None or c.fecha_baja <= dia):
                continue
            out.append(rfc)
        return out

    def mrr(self, dia: date) -> float:
        m = _mes(dia)
        ant = _mes_anterior(m)
        tot = 0.0
        for rfc in self.recurrentes(dia):
            v = self.por_cli.get((m, rfc))
            if v is None:
                v = self.por_cli.get((ant, rfc), 0.0)
            tot += v
        return tot

    def mrr_mes(self, per: str) -> float:
        """Recurrente facturado en un mes completo (para la base). Incluye clientes que después se dieron de baja."""
        return sum(self.por_cli.get((per, rfc), 0.0) for rfc, c in self.estatus.items() if c.recurrente)

    def total_cerrado(self, dia: date) -> tuple[str, float] | None:
        per = _mes_anterior(_mes(dia))
        if per not in self.por_mes:
            return None
        return per, self.por_mes[per]


def periodo_base(d: Datos, v: MciVersion) -> list[str]:
    if v.base_desde and v.base_hasta:
        return _meses_entre(v.base_desde, v.base_hasta)
    return d.meses[:3]


def base_monto(d: Datos, v: MciVersion) -> float | None:
    meses = periodo_base(d, v)
    if not meses:
        return None
    vals = [d.mrr_mes(p) if v.fuente == "cfdi_recurrente" else d.por_mes.get(p, 0.0) for p in meses]
    vals = [x for x in vals if x]
    return sum(vals) / len(vals) if vals else None


def valor_actual(d: Datos, v: MciVersion, dia: date) -> float | None:
    if v.fuente == "cfdi_recurrente":
        return d.mrr(dia) if d.estatus else None
    t = d.total_cerrado(dia)
    return t[1] if t else None


def medir(db: Session, semanas: int = 26) -> int:
    """Guarda (o reemplaza) el % de crecimiento mensual de cada MCI con fuente CFDI."""
    db.expire_all()  # las versiones recién creadas deben verse en m.versiones
    d = Datos(db)
    if not d.meses:
        return 0
    n = 0
    for m in db.scalars(select(Mci).where(Mci.activo.is_(True))):
        if not m.versiones:
            continue
        for sem in indicadores.meses(max(semanas // 4, 1)):  # la MCI se mide por mes
            dia = min(indicadores.ultimo_dia(sem), hoy())
            v = catalogo.version_vigente(m.versiones, dia)
            if not v or v.fuente not in ("cfdi_recurrente", "cfdi_total"):
                continue
            base = base_monto(d, v)
            act = valor_actual(d, v, min(dia, hoy()))
            if not base or act is None:
                continue
            pct = (act / base - 1) * 100
            med = db.scalar(select(Medicion).where(Medicion.tipo == "mci",
                                                   Medicion.version_id.in_([x.id for x in m.versiones]),
                                                   Medicion.periodo == sem))
            if med:
                med.valor, med.version_id, med.fuente = round(pct, 4), v.id, "cfdi"
            else:
                db.add(Medicion(tipo="mci", version_id=v.id, periodo=sem, valor=round(pct, 4), fuente="cfdi"))
            n += 1
    db.commit()
    return n


@dataclass
class Resumen:
    fuente: str
    pct: float | None
    base: float | None
    actual: float | None
    meta_pct: float | None
    meta_monto: float | None
    falta: float | None
    meses_restantes: int | None
    necesario_mensual: float | None
    ritmo_pct: float | None
    brecha_monto: float | None  # contra el ritmo de hoy; negativo = abajo
    periodo_base: str
    fecha_meta: date | None


def resumen(db: Session, m: Mci, datos: Datos | None = None) -> Resumen | None:
    v = catalogo.version_vigente(m.versiones)
    if not v or v.fuente not in ("cfdi_recurrente", "cfdi_total"):
        return None
    d = datos or Datos(db)
    base = base_monto(d, v)
    act = valor_actual(d, v, hoy())
    pct = (act / base - 1) * 100 if base and act is not None else None
    meta_pct = float(v.meta) if v.meta is not None else None
    meta_monto = base * (1 + meta_pct / 100) if base and meta_pct is not None else None
    falta = meta_monto - act if meta_monto is not None and act is not None else None
    meses = None
    if v.fecha_meta:
        meses = max(1, (v.fecha_meta.year - hoy().year) * 12 + v.fecha_meta.month - hoy().month)
    necesario = falta / meses if falta is not None and meses else None
    ritmo = indicadores.objetivo(v, hoy())
    brecha = (act - base * (1 + ritmo / 100)) if (base and act is not None and ritmo is not None) else None
    pb = periodo_base(d, v)
    return Resumen(fuente=v.fuente, pct=pct, base=base, actual=act, meta_pct=meta_pct, meta_monto=meta_monto,
                   falta=falta, meses_restantes=meses, necesario_mensual=necesario, ritmo_pct=ritmo,
                   brecha_monto=brecha, periodo_base=f"{pb[0]} a {pb[-1]}" if pb else "sin datos",
                   fecha_meta=v.fecha_meta)


def es_auto(m: Mci) -> bool:
    v = catalogo.version_vigente(m.versiones)
    return bool(v and v.fuente in ("cfdi_recurrente", "cfdi_total"))

"""Extracción automática de ventas desde la base de CFDI (solo lectura).

La consulta se define en CFDI_VENTAS_SQL para adaptarse al esquema real. Se agregan
ventas por mes y por cliente; el detalle por cliente es confidencial."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ClienteEstatus, VentaClienteMes, VentaMensual, utcnow
from .util import auditar, hoy


def _a_fecha(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:10]).date()


def agregar(filas: list[dict]) -> tuple[dict, dict]:
    """Devuelve (por_mes, por_cliente_mes). Convierte a MXN con el tipo de cambio del CFDI."""
    por_mes: dict[str, dict] = defaultdict(lambda: {"total": Decimal(0), "facturas": 0, "clientes": set()})
    por_cli: dict[tuple[str, str], dict] = defaultdict(lambda: {"total": Decimal(0), "facturas": 0, "nombre": ""})
    primera: dict[str, str] = {}
    for f in sorted(filas, key=lambda x: _a_fecha(x["fecha"])):
        d = _a_fecha(f["fecha"])
        per = f"{d.year}-{d.month:02d}"
        rfc = str(f.get("rfc_receptor") or "").strip().upper()
        sub = Decimal(str(f.get("subtotal") or 0))
        mon = str(f.get("moneda") or "MXN").upper()
        tc = Decimal(str(f.get("tipo_cambio") or 1)) if mon not in ("MXN", "XXX") else Decimal(1)
        mxn = sub * tc
        m = por_mes[per]
        m["total"] += mxn
        m["facturas"] += 1
        m["clientes"].add(rfc)
        primera.setdefault(rfc, per)
        c = por_cli[(per, rfc)]
        c["total"] += mxn
        c["facturas"] += 1
        c["nombre"] = str(f.get("nombre_receptor") or c["nombre"])[:255]
    for per, m in por_mes.items():
        nuevos = [r for r in m["clientes"] if primera.get(r) == per]
        m["clientes_nuevos"] = len(nuevos)
        m["total_nuevos"] = sum((por_cli[(per, r)]["total"] for r in nuevos), Decimal(0))
    return por_mes, por_cli


def sincronizar(db: Session, meses: int = 24) -> int:
    s = get_settings()
    if not s.cfdi_database_url or not s.cfdi_ventas_sql:
        raise RuntimeError("Configura CFDI_DATABASE_URL y CFDI_VENTAS_SQL en .env")
    fin = hoy().replace(day=1)
    y, m = fin.year, fin.month + 1
    hasta = date(y + (m > 12), (m - 1) % 12 + 1, 1)
    yd, md = fin.year, fin.month - (meses - 1)
    while md <= 0:
        md += 12
        yd -= 1
    desde = date(yd, md, 1)
    eng = create_engine(s.cfdi_database_url, pool_pre_ping=True)
    with eng.connect() as cx:
        filas = [dict(r._mapping) for r in cx.execute(text(s.cfdi_ventas_sql), {"desde": desde, "hasta": hasta})]
    por_mes, por_cli = agregar(filas)
    # Los clientes "nuevos" solo son confiables dentro de la ventana: el primer mes se marca sin nuevos.
    primer = min(por_mes) if por_mes else None
    for per, v in por_mes.items():
        row = db.get(VentaMensual, per) or VentaMensual(periodo=per)
        row.total_mxn = float(round(v["total"], 2))
        row.facturas = v["facturas"]
        row.clientes = len(v["clientes"])
        row.clientes_nuevos = 0 if per == primer else v["clientes_nuevos"]
        row.total_nuevos_mxn = 0 if per == primer else float(round(v["total_nuevos"], 2))
        row.sincronizado = utcnow()
        db.merge(row)
    db.execute(delete(VentaClienteMes).where(VentaClienteMes.periodo.in_(list(por_mes))))
    for (per, rfc), v in por_cli.items():
        db.add(VentaClienteMes(periodo=per, rfc=rfc[:13], nombre=v["nombre"], total_mxn=float(round(v["total"], 2)),
                               facturas=v["facturas"]))
    n_cli = 0
    if s.cfdi_clientes_sql:
        with eng.connect() as cx:
            cli = [dict(r._mapping) for r in cx.execute(text(s.cfdi_clientes_sql))]
        db.execute(delete(ClienteEstatus))
        for c in cli:
            rfc = str(c.get("rfc") or "").strip().upper()[:13]
            if not rfc:
                continue
            fb = c.get("fecha_baja")
            db.add(ClienteEstatus(rfc=rfc, recurrente=bool(c.get("es_recurrente")), baja=bool(c.get("es_baja")),
                                  fecha_baja=_a_fecha(fb) if fb else None))
            n_cli += 1
    auditar(db, "sync_cfdi", "ventas", None, None, actor="agente", filas=len(filas), meses=len(por_mes), clientes=n_cli)
    db.commit()
    from . import ventas_auto
    ventas_auto.medir(db)
    return len(filas)

"""Indicadores, presentación semanal, descargas en Excel, vista del socio y perfil."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Area,
    Compromiso,
    Excepcion,
    Idea,
    PublicacionSocio,
    Usuario,
    VentaClienteMes,
    VentaMensual,
    utcnow,
)
from ..services import seguimiento
from ..services.util import auditar, hoy, semana_iso, semanas_atras
from .base import (OptDate, check_password, flash, hash_password, ir, render, requiere_direccion, requiere_lider,
                   usuario_actual)

router = APIRouter()


def _cumpl_areas(db: Session, semanas: list[str]) -> list[dict]:
    excs = seguimiento.excepciones_aprobadas(db)
    out = []
    for a in db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)):
        comps = list(db.scalars(select(Compromiso).where(Compromiso.area_id == a.id, Compromiso.semana.in_(semanas))))
        c = seguimiento.cumplimiento(comps, excs)
        out.append({"area": a, "cumpl": c, "total": len(comps)})
    return out


def _variacion(actual: float, anterior: float) -> float | None:
    return (actual / anterior - 1) * 100 if anterior else None


# ---------------------------------------------------------------- indicadores

@router.get("/indicadores")
def indicadores(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    ventas = list(db.scalars(select(VentaMensual).order_by(VentaMensual.periodo.desc()).limit(13)))[::-1]
    serie = []
    for i, v in enumerate(ventas):
        prev = ventas[i - 1] if i else None
        serie.append({"periodo": v.periodo, "total": float(v.total_mxn) if u.puede_confidencial else None,
                      "var": _variacion(float(v.total_mxn), float(prev.total_mxn)) if prev else None,
                      "clientes": v.clientes, "nuevos": v.clientes_nuevos, "facturas": v.facturas})
    w4 = semanas_atras(4)
    acept = seguimiento.aceptacion_ia(db, hoy() - timedelta(days=30))
    return render(request, "indicadores.html", u, "indicadores", serie=serie, areas=_cumpl_areas(db, w4),
                  acept=acept, sincronizado=ventas[-1].sincronizado if ventas else None)


# ---------------------------------------------------------------- presentación semanal

@router.get("/semanal")
def semanal(request: Request, tipo: str = "semana", semana: str | None = None, mes: str | None = None,
            desde: OptDate = None, hasta: OptDate = None, area: str | None = None,
            db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    """Rendición de cuentas en PDF 16:9: vista general por área y, por área, sus compromisos por medida predictiva."""
    from ..services import indicadores, reporte_semanal as rs
    tipo = tipo if tipo in ("semana", "mes", "rango") else "semana"
    if tipo == "semana" and not semana:
        semana = semana_iso(hoy() - timedelta(days=7) if hoy().weekday() < 2 else hoy())
        if semana < indicadores.inicio_seguimiento():
            semana = semana_iso(hoy())  # antes del arranque no hay junta que revisar
    sems, etiqueta, mes_ref = rs.periodo(tipo, semana, mes, desde, hasta)
    todas = [a for a in db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.id))
             if indicadores.mcis_de(db, a.id) and (u.es_direccion or a.id == u.area_id)]
    reportes_ = [rs.area_reporte(db, a, sems) for a in todas if not area or a.clave == area]
    lista_sem = semanas_atras(12)
    if semana and semana not in lista_sem:
        lista_sem = sorted(set(lista_sem) | {semana}, reverse=True)
    return render(request, "semanal.html", u, "semanal", tipo=tipo, sem=semana or sems[-1], sems=sems, etiqueta=etiqueta,
                  mes=mes_ref, reportes=reportes_, semanas=lista_sem, meses=indicadores.meses_disponibles()[::-1],
                  nombre_mes=indicadores.nombre_mes, areas=todas, area_sel=area or "", desde=desde, hasta=hasta,
                  generado=utcnow())


# ---------------------------------------------------------------- descargas Excel

AMARILLO = PatternFill("solid", fgColor="FFD400")


def _xlsx(nombre: str, hojas: dict[str, tuple[list[str], list[list]]]) -> StreamingResponse:
    wb = Workbook()
    wb.remove(wb.active)
    for titulo, (cab, filas) in hojas.items():
        ws = wb.create_sheet(titulo[:31])
        ws.append(cab)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = AMARILLO
            c.alignment = Alignment(vertical="center")
        for f in filas:
            ws.append(f)
        for i, _ in enumerate(cab, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 22
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/reportes")
def reportes(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    return render(request, "reportes.html", u, "reportes")


def _rango(desde: date | None, hasta: date | None) -> tuple[date, date]:
    return desde or hoy() - timedelta(days=56), hasta or hoy()


@router.get("/reportes/compromisos.xlsx")
def rep_compromisos(desde: date | None = None, hasta: date | None = None, db: Session = Depends(get_db),
                    u: Usuario = Depends(usuario_actual)):
    d, h = _rango(desde, hasta)
    q = select(Compromiso).where(Compromiso.fecha_limite.between(d, h))
    if not u.es_direccion:
        q = q.where(Compromiso.area_id == u.area_id) if u.es_lider else q.where(Compromiso.responsable_id == u.id)
    excs = seguimiento.excepciones_aprobadas(db)
    filas = []
    for c in db.scalars(q.order_by(Compromiso.semana, Compromiso.area_id)):
        r = c.remediacion
        e = seguimiento.esta_exceptuado(c, excs)
        filas.append([c.semana, c.area.nombre, c.responsable.nombre, c.predictiva.nombre if c.predictiva else "",
                      c.predictiva_version.version if c.predictiva_version else "", c.estrategia.nombre if c.estrategia else "",
                      c.texto, c.fecha_limite, c.estado, c.avance, r.causa if r else "", r.causa_categoria if r else "",
                      r.accion if r else "", r.nueva_fecha if r else None, r.estado if r else "",
                      f"Sí ({e.categoria})" if e else ""])
    auditar(db, "descarga", "reporte_compromisos", None, u.id, desde=str(d), hasta=str(h))
    db.commit()
    return _xlsx(f"compromisos_{d}_{h}.xlsx", {"Compromisos": (
        ["Semana", "Área", "Responsable", "Medida predictiva", "Versión medida", "Estrategia", "Compromiso",
         "Fecha límite", "Estado", "Avance %", "Causa", "Categoría causa", "Remediación", "Nueva fecha",
         "Estado remediación", "Exceptuado"], filas)})


@router.get("/reportes/participacion.xlsx")
def rep_participacion(db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    personas = seguimiento.estado_personas(db, None if u.es_direccion else u.area_id)
    filas = [[e.usuario.nombre, e.usuario.area.nombre if e.usuario.area else "", e.semana,
              round(e.cumpl.pct, 1) if e.cumpl.pct is not None else None, e.cumpl.cumplidos, e.cumpl.no_cumplidos,
              e.cumpl.exceptuados, e.vencidos, e.sin_remediar, e.dias_sin_captura, e.ideas_mes, e.ideas_debiles,
              e.estado] for e in personas]
    auditar(db, "descarga", "reporte_participacion", None, u.id)
    db.commit()
    return _xlsx(f"participacion_{hoy()}.xlsx", {"Participación": (
        ["Persona", "Área", "Compromisos semana", "Cumplimiento 4 sem %", "Cumplidos", "No cumplidos", "Exceptuados",
         "Vencidos", "Sin remediar", "Días sin captura", "Ideas del mes", "Ideas débiles", "Estado"], filas)})


@router.get("/reportes/ideas.xlsx")
def rep_ideas(db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    filas = [[i.creado.date(), i.autor.nombre, i.area.nombre, i.titulo, i.problema, i.beneficio, i.origen,
              i.impacto, i.esfuerzo, i.cuadrante, i.fortaleza, i.estado, i.avance]
             for i in db.scalars(select(Idea).order_by(Idea.creado.desc()))]
    auditar(db, "descarga", "reporte_ideas", None, u.id)
    db.commit()
    return _xlsx(f"ideas_{hoy()}.xlsx", {"Ideas": (
        ["Fecha", "Autor", "Área", "Título", "Problema", "Beneficio", "Origen", "Impacto", "Esfuerzo", "Cuadrante",
         "Fortaleza", "Estado", "Avance %"], filas)})


@router.get("/reportes/ventas_clientes.xlsx")
def rep_ventas_clientes(meses: int = 12, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    if not u.puede_confidencial:
        auditar(db, "descarga_denegada", "reporte_ventas_clientes", None, u.id)
        db.commit()
        raise HTTPException(403, "Este reporte es confidencial.")
    desde = (hoy().replace(day=1) - timedelta(days=31 * (meses - 1))).strftime("%Y-%m")
    filas = [[v.periodo, v.rfc, v.nombre, float(v.total_mxn), v.facturas]
             for v in db.scalars(select(VentaClienteMes).where(VentaClienteMes.periodo >= desde)
                                 .order_by(VentaClienteMes.periodo, VentaClienteMes.total_mxn.desc()))]
    mens = [[v.periodo, float(v.total_mxn), v.facturas, v.clientes, v.clientes_nuevos, float(v.total_nuevos_mxn)]
            for v in db.scalars(select(VentaMensual).where(VentaMensual.periodo >= desde).order_by(VentaMensual.periodo))]
    auditar(db, "descarga", "reporte_ventas_clientes", None, u.id, meses=meses)
    db.commit()
    return _xlsx(f"ventas_clientes_CONFIDENCIAL_{hoy()}.xlsx", {
        "Ventas por mes": (["Periodo", "Total MXN", "Facturas", "Clientes", "Clientes nuevos", "Venta nuevos MXN"], mens),
        "Por cliente": (["Periodo", "RFC", "Cliente", "Total MXN", "Facturas"], filas)})


# ---------------------------------------------------------------- vista del socio

def _corte_socio(db: Session, periodo: str) -> dict:
    """Corte consolidado por área. Mismos datos; las excepciones aprobadas visibles se muestran etiquetadas."""
    y, m = map(int, periodo.split("-"))
    ini = date(y, m, 1)
    fin = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
    excs = seguimiento.excepciones_aprobadas(db)
    areas = []
    for a in db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre)):
        comps = list(db.scalars(select(Compromiso).where(Compromiso.area_id == a.id,
                                                         Compromiso.fecha_limite.between(ini, fin))))
        c = seguimiento.cumplimiento(comps, excs)
        rem = [x for x in comps if x.remediacion]
        rem_ok = sum(1 for x in rem if x.remediacion.estado == "cumplida")
        areas.append({"area": a.nombre, "pct": round(c.pct, 1) if c.pct is not None else None,
                      "cumplidos": c.cumplidos, "no_cumplidos": c.no_cumplidos, "exceptuados": c.exceptuados,
                      "remediaciones": len(rem), "remediaciones_cumplidas": rem_ok})
    vis = [e for e in excs if e.visible_socio and e.periodo_desde <= fin and e.periodo_hasta >= ini]
    v = db.get(VentaMensual, periodo)
    prev_p = f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
    vp = db.get(VentaMensual, prev_p)
    return {"periodo": periodo, "areas": areas,
            "excepciones": [{"categoria": e.categoria, "justificacion": e.justificacion,
                             "efecto": e.efecto, "meta_ajustada": float(e.meta_ajustada) if e.meta_ajustada else None}
                            for e in vis],
            "ventas": {"total": float(v.total_mxn) if v else None, "clientes": v.clientes if v else None,
                       "nuevos": v.clientes_nuevos if v else None,
                       "var": _variacion(float(v.total_mxn), float(vp.total_mxn)) if v and vp else None}}


@router.get("/socio")
def socio(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    pubs = list(db.scalars(select(PublicacionSocio).order_by(PublicacionSocio.creado.desc()).limit(24)))
    return render(request, "socio.html", u, "socio", pubs=pubs, periodo_def=(hoy().replace(day=1) - timedelta(days=1)).strftime("%Y-%m"))


@router.post("/socio")
def socio_crear(request: Request, periodo: str = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    try:
        datetime.strptime(periodo, "%Y-%m")
    except ValueError:
        raise HTTPException(400, "Periodo con formato AAAA-MM.")
    p = PublicacionSocio(periodo=periodo, titulo=f"Corte ejecutivo {periodo}", contenido=_corte_socio(db, periodo),
                         creado_por=u.id)
    db.add(p)
    db.flush()
    auditar(db, "crear", "publicacion_socio", p.id, u.id)
    db.commit()
    return ir(f"/socio/{p.id}")


@router.get("/socio/{pid}")
def socio_ver(pid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    p = db.get(PublicacionSocio, pid)
    if not p:
        raise HTTPException(404)
    return render(request, "socio_corte.html", u, "socio", p=p)


@router.post("/socio/{pid}")
def socio_guardar(pid: int, request: Request, narrativa: str = Form(""), accion: str = Form("guardar"),
                  db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    p = db.get(PublicacionSocio, pid)
    if not p or p.estado == "publicada":
        raise HTTPException(400, "El corte ya está publicado; crea uno nuevo.")
    p.narrativa = narrativa
    if accion == "publicar":
        p.estado, p.publicada_en = "publicada", utcnow()
    auditar(db, accion, "publicacion_socio", p.id, u.id)
    db.commit()
    flash(request, "Corte publicado; queda congelado." if accion == "publicar" else "Borrador guardado.")
    return ir(f"/socio/{pid}")


# ---------------------------------------------------------------- perfil

@router.get("/perfil")
def perfil(request: Request, u: Usuario = Depends(usuario_actual)):
    return render(request, "perfil.html", u, "")


@router.post("/perfil/password")
def cambiar_password(request: Request, actual: str = Form(...), nueva: str = Form(...), db: Session = Depends(get_db),
                     u: Usuario = Depends(usuario_actual)):
    if not check_password(actual, u.password_hash):
        flash(request, "La contraseña actual no coincide.", "error")
    elif len(nueva) < 10:
        flash(request, "La nueva contraseña debe tener al menos 10 caracteres.", "error")
    else:
        u.password_hash = hash_password(nueva)
        auditar(db, "cambiar_password", "usuario", u.id, u.id)
        db.commit()
        flash(request, "Contraseña actualizada.")
    return ir("/perfil")

"""GuardIA Ticket: informe de horas y costos, pagos a ingenieros y «mis horas y pagos»."""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TICKET_ESTADOS, TicketPago, Usuario
from ..services import accesos, almacen
from ..services import subidas as subidas_srv
from ..services import tickets as tk
from ..services import tickets_costos as tc
from .base import OptDate, OptInt, flash, ir, render, usuario_actual

router = APIRouter()
log = logging.getLogger("bitacora.web.tickets_pagos")

TIPOS_COMPROBANTE = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "webp": "image/webp", "xml": "application/xml", "zip": "application/zip"}
MAX_COMPROBANTE = 50 * 1024 * 1024  # sin S3; con S3 sube directo y no hay límite práctico


def _exigir(u: Usuario, clave: str) -> None:
    if not accesos.puede(u, clave):
        raise HTTPException(403, "No tienes permiso para esta sección.")


# ---------------------------------------------------------------- informe

@router.get("/tickets-informe")
def informe(request: Request, mes: str = "", desde: OptDate = None, hasta: OptDate = None, formato: str = "",
            db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-informe")
    d1, d2, etiqueta = tc.periodo(mes, desde, hasta)
    r = tc.informe(db, d1, d2)
    if formato == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Folio", "Título", "Módulo", "Solicitante", "Ingeniero", "Cerrado", "Horas estimadas", "Horas reales",
                    "Tarifa", "Costo", "Pagado"])
        for t in r["tickets"]:
            w.writerow([t.folio, t.titulo, t.producto, t.autor.nombre if t.autor else "",
                        t.asignado.nombre if t.asignado else "", t.cerrado_en.strftime("%Y-%m-%d") if t.cerrado_en else "",
                        float(t.horas_estimadas or 0), float(t.horas_reales or 0), float(t.costo_hora or 0),
                        t.costo or 0, "sí" if t.pago_id else "no"])
        nombre = f"horas-costos_{d1:%Y%m%d}-{d2:%Y%m%d}.csv"
        return Response("﻿" + buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nombre)}"})
    return render(request, "tickets_informe.html", u, "tickets-informe", r=r, d1=d1, d2=d2, etiqueta=etiqueta,
                  mes=mes or f"{d1:%Y-%m}", rango=bool(desde and hasta), estados=TICKET_ESTADOS, hoy=date.today())


# ---------------------------------------------------------------- pagos (Dirección)

@router.get("/tickets-pagos")
def pagos(request: Request, ingeniero: OptInt = None, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-pagos")
    grupos = tc.por_pagar(db)
    return render(request, "tickets_pagos.html", u, "tickets-pagos", grupos=grupos, pagos=tc.pagos(db, ingeniero),
                  ingeniero=ingeniero, gente=tk.asignables(db), s3=subidas_srv.disponible(),
                  aceptar=",".join("." + e for e in TIPOS_COMPROBANTE), hoy=date.today())


async def _comprobante(archivo: UploadFile | None, token: str, uid: int) -> dict | None:
    if token:
        return subidas_srv.recibir(token, uid, "pago")
    if archivo and archivo.filename:
        ext = archivo.filename.rsplit(".", 1)[-1].lower() if "." in archivo.filename else ""
        if ext not in TIPOS_COMPROBANTE:
            raise tc.PagoInvalido("El comprobante debe ser PDF, imagen, XML o ZIP.")
        contenido = await archivo.read()
        if not contenido:
            raise tc.PagoInvalido("El comprobante está vacío.")
        if len(contenido) > MAX_COMPROBANTE:
            raise tc.PagoInvalido("El comprobante pasa de 50 MB. Comprímelo o pide a sistemas activar S3.")
        ruta = almacen.guardar("pagos", archivo.filename, contenido, TIPOS_COMPROBANTE[ext])
        return {"ruta": ruta, "nombre": archivo.filename, "tipo": TIPOS_COMPROBANTE[ext], "bytes": len(contenido)}
    return None


@router.post("/tickets-pagos")
async def registrar(request: Request, ingeniero_id: OptInt = Form(None), tickets: list[int] = Form([]),
                    fecha: OptDate = Form(None), monto: str = Form(""), nota: str = Form(""),
                    comprobante: UploadFile | None = File(None), subidas: list[str] = Form([]),
                    db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-pagos")
    ing = db.get(Usuario, ingeniero_id) if ingeniero_id else None
    if not ing:
        flash(request, "Elige al ingeniero.", "error")
        return ir("/tickets-pagos")
    comp = None
    try:
        comp = await _comprobante(comprobante, subidas[0] if subidas else "", u.id)
        p = tc.registrar(db, u, ingeniero=ing, ticket_ids=tickets, fecha=fecha, monto=monto, nota=nota, comprobante=comp)
        db.commit()
    except (tc.PagoInvalido, subidas_srv.SubidaInvalida, almacen.AlmacenError) as e:
        db.rollback()
        if comp and not subidas:
            almacen.borrar(comp["ruta"])
        flash(request, str(e), "error")
        return ir("/tickets-pagos")
    flash(request, f"Pago a {ing.nombre} registrado: ${float(p.monto):,.2f} por {len(p.tickets)} ticket(s).")
    return ir(f"/tickets-pagos#pago-{p.id}")


def _descargar_comprobante(p: TicketPago):
    if not p.comprobante_ruta:
        raise HTTPException(404, "Este pago no tiene comprobante.")
    if (url := almacen.url_descarga(p.comprobante_ruta, p.comprobante_nombre, p.comprobante_tipo)):
        return RedirectResponse(url, status_code=302)
    try:
        contenido = almacen.leer(p.comprobante_ruta)
    except almacen.AlmacenError as e:
        raise HTTPException(404, str(e)) from e
    return Response(contenido, media_type=p.comprobante_tipo or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(p.comprobante_nombre)}",
                             "X-Content-Type-Options": "nosniff"})


@router.get("/tickets-pagos/{pid}/comprobante")
def comprobante(pid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-pagos")
    p = db.get(TicketPago, pid)
    if not p:
        raise HTTPException(404)
    return _descargar_comprobante(p)


# ---------------------------------------------------------------- el ingeniero ve lo suyo

@router.get("/tickets-tablero/mis-pagos")
def mis_pagos(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    pend = tc.por_pagar(db, u.id)
    return render(request, "tickets_mis_pagos.html", u, "tickets-tablero", pendiente=pend[0] if pend else None,
                  pagos=tc.pagos(db, u.id))


@router.get("/tickets-tablero/mis-pagos/{pid}/comprobante")
def mi_comprobante(pid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    _exigir(u, "tickets-tablero")
    p = db.get(TicketPago, pid)
    if not p or p.ingeniero_id != u.id:
        raise HTTPException(404)
    return _descargar_comprobante(p)

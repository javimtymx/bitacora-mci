"""Sesiones con cliente (evidencia + análisis IA) y módulo Voz del Cliente."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CUADRANTES, Area, Compromiso, Idea, SesionCliente, Usuario, VentaClienteMes, utcnow
from ..services import ia, vozcliente
from ..services.util import auditar, hoy
from .base import flash, ir, puede_actuar, render, requiere_lider, usuario_actual, OptInt

router = APIRouter()
MAX_BYTES = 2 * 1024 * 1024


def _clientes(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(select(VentaClienteMes.rfc, VentaClienteMes.nombre).distinct().order_by(VentaClienteMes.nombre)).all()
    vistos, out = set(), []
    for rfc, nombre in rows:
        if rfc not in vistos:
            vistos.add(rfc)
            out.append((rfc, nombre or rfc))
    return out


def _puede_ver(u: Usuario, s: SesionCliente) -> bool:
    return u.es_direccion or s.autor_id == u.id or (u.rol == "lider" and u.area_id == s.area_id)


def etiqueta_cliente(u: Usuario, s: SesionCliente) -> str:
    if u.puede_confidencial:
        return s.cliente_nombre or s.cliente_rfc or ("Prospecto sin nombre" if s.tipo == "venta_nueva" else "Cliente sin nombre")
    return f"{'Prospecto' if s.tipo == 'venta_nueva' else 'Cliente'} #{s.id:04d}"


@router.get("/sesiones/nueva")
def nueva(request: Request, compromiso: int | None = None, db: Session = Depends(get_db),
          u: Usuario = Depends(usuario_actual)):
    c = db.get(Compromiso, compromiso) if compromiso else None
    if c and not puede_actuar(u, c):
        raise HTTPException(403)
    tipo = "objeciones" if (c and c.area and c.area.clave == "ventas") else "retencion"
    if c and c.predictiva and "prospect" in c.predictiva.nombre.lower():
        tipo = "venta_nueva"
    return render(request, "sesion_nueva.html", u, "voz", c=c, tipo=tipo, tipos=vozcliente.TIPOS_SESION, clientes=_clientes(db) if u.puede_confidencial else [],
                  areas=list(db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.nombre))))


@router.post("/sesiones")
async def crear_sesion(request: Request, tipo: str = Form(...), fecha: date = Form(...), participantes: str = Form(""),
                       cliente: str = Form(""), cliente_nombre: str = Form(""), compromiso_id: OptInt = Form(None),
                       area_id: OptInt = Form(None), consentimiento: bool = Form(False),
                       p_nombre: list[str] = Form([]), p_email: list[str] = Form([]), p_lado: list[str] = Form([]),
                       archivo: UploadFile = File(...), db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    if tipo not in vozcliente.TIPOS_SESION:
        raise HTTPException(400)
    c = db.get(Compromiso, compromiso_id) if compromiso_id else None
    if c and not puede_actuar(u, c):
        raise HTTPException(403)
    raw = await archivo.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(400, "La transcripción pasa de 2 MB; súbela en texto plano (.txt).")
    if not (archivo.filename or "").lower().endswith((".txt", ".vtt", ".srt", ".md")):
        raise HTTPException(400, "Sube la transcripción como archivo de texto (.txt).")
    texto = vozcliente.decodificar(raw).strip()
    if len(texto.split()) < 80:
        raise HTTPException(400, "La transcripción es demasiado corta para analizarla (menos de 80 palabras).")
    rfc, nombre = "", cliente_nombre.strip()[:255]
    if cliente and "|" in cliente:
        rfc, nombre = cliente.split("|", 1)
    s = SesionCliente(tipo=tipo, area_id=(c.area_id if c else (area_id or u.area_id)), compromiso_id=c.id if c else None,
                      cliente_rfc=rfc[:13], cliente_nombre=nombre[:255], fecha=fecha, participantes=participantes[:300],
                      consentimiento=consentimiento, autor_id=u.id, transcripcion="",
                      participantes_det=vozcliente.limpiar_participantes(
                          [{"nombre": n, "email": (p_email[i] if i < len(p_email) else ""),
                            "lado": (p_lado[i] if i < len(p_lado) else "cliente")} for i, n in enumerate(p_nombre)]))
    if not s.participantes and s.participantes_det:
        s.participantes = ", ".join(p["nombre"] for p in s.participantes_det)[:300]
    if not s.area_id:
        raise HTTPException(400, "Indica el área.")
    vozcliente.guardar_texto(s, texto)
    db.add(s)
    db.flush()
    auditar(db, "cargar", "sesion_cliente", s.id, u.id, compromiso=s.compromiso_id, palabras=s.palabras)
    db.commit()
    try:
        vozcliente.analizar(db, s)
        flash(request, "Sesión analizada." + (f" Se descartaron {s.citas_descartadas} citas que no aparecen tal cual en la transcripción." if s.citas_descartadas else ""))
    except (ia.IANoDisponible, ValueError) as e:
        s.estado = "error"
        db.commit()
        flash(request, f"La transcripción se guardó, pero la IA no pudo analizarla: {str(e)[:160]}. Reintenta desde la sesión.", "error")
    return ir(f"/sesiones/{s.id}")


@router.get("/sesiones/{sid}")
def ver_sesion(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    s = db.get(SesionCliente, sid)
    if not s or not _puede_ver(u, s):
        raise HTTPException(404)
    return render(request, "sesion.html", u, "voz", s=s, cliente=etiqueta_cliente(u, s), cuadrantes=CUADRANTES,
                  tipos=vozcliente.TIPOS_SESION, ve_personas=(u.puede_confidencial or u.es_direccion or s.autor_id == u.id),
                  puede_cerrar=bool(s.compromiso and puede_actuar(u, s.compromiso) and s.compromiso.estado == "abierto"))


@router.post("/sesiones/{sid}/participantes")
def editar_participantes(sid: int, request: Request, p_nombre: list[str] = Form([]), p_email: list[str] = Form([]),
                         p_lado: list[str] = Form([]), p_rol: list[str] = Form([]), db: Session = Depends(get_db),
                         u: Usuario = Depends(usuario_actual)):
    s = db.get(SesionCliente, sid)
    if not s or not _puede_ver(u, s) or not (u.puede_confidencial or u.es_direccion or s.autor_id == u.id):
        raise HTTPException(403)
    prev = {p["nombre"].lower(): p.get("origen", "captura") for p in (s.participantes_det or [])}
    s.participantes_det = vozcliente.limpiar_participantes(
        [{"nombre": n, "email": p_email[i] if i < len(p_email) else "", "lado": p_lado[i] if i < len(p_lado) else "cliente",
          "rol": p_rol[i] if i < len(p_rol) else "", "origen": prev.get(n.strip().lower(), "captura")}
         for i, n in enumerate(p_nombre) if n.strip()])
    s.participantes = ", ".join(p["nombre"] for p in s.participantes_det)[:300]
    auditar(db, "editar_participantes", "sesion_cliente", s.id, u.id, n=len(s.participantes_det))
    db.commit()
    flash(request, "Participantes actualizados.")
    return ir(f"/sesiones/{s.id}")


@router.get("/sesiones/{sid}/transcripcion")
def ver_transcripcion(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    s = db.get(SesionCliente, sid)
    if not s or not (u.es_direccion or s.autor_id == u.id):
        raise HTTPException(403, "La transcripción completa solo la ven Dirección y quien la cargó.")
    auditar(db, "ver_transcripcion", "sesion_cliente", s.id, u.id)
    db.commit()
    return render(request, "transcripcion.html", u, "voz", s=s, texto=vozcliente.leer_texto(s), cliente=etiqueta_cliente(u, s))


@router.post("/sesiones/{sid}/analizar")
def reanalizar(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    s = db.get(SesionCliente, sid)
    if not s or not _puede_ver(u, s):
        raise HTTPException(404)
    try:
        vozcliente.analizar(db, s)
        flash(request, "Sesión analizada de nuevo.")
    except (ia.IANoDisponible, ValueError) as e:
        flash(request, f"La IA no pudo analizarla: {str(e)[:160]}", "error")
    return ir(f"/sesiones/{sid}")


@router.post("/sesiones/{sid}/cumplido")
def cerrar_con_evidencia(sid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    s = db.get(SesionCliente, sid)
    if not s or not s.compromiso or not puede_actuar(u, s.compromiso):
        raise HTTPException(403)
    if not s.evidencia_verificada:
        flash(request, "La IA no confirmó que la sesión corresponda al compromiso, o falta el consentimiento del cliente.", "error")
        return ir(f"/sesiones/{sid}")
    c = s.compromiso
    c.estado, c.avance, c.cerrado = "cumplido", 100, utcnow()
    auditar(db, "cumplido_con_evidencia", "compromiso", c.id, u.id, sesion=s.id)
    db.commit()
    flash(request, "Compromiso cumplido con evidencia.")
    return ir(f"/compromisos/{c.id}")


@router.post("/hallazgos/{hid}/idea")
def hallazgo_a_idea(hid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    from ..models import Hallazgo
    h = db.get(Hallazgo, hid)
    if not h or not _puede_ver(u, h.sesion):
        raise HTTPException(404)
    innov = db.scalar(select(Area).where(Area.clave == "innovacion"))
    i = Idea(autor_id=u.id, area_id=innov.id if innov else h.sesion.area_id, titulo=h.tema[:200],
             problema=f"Voz del cliente ({CUADRANTES.get(h.cuadrante, h.cuadrante)}): «{h.cita}»",
             beneficio="", origen="objecion_cliente" if h.sesion.tipo in ("objeciones", "venta_nueva") else "queja")
    db.add(i)
    auditar(db, "hallazgo_a_idea", "hallazgo", h.id, u.id)
    db.commit()
    flash(request, "Hallazgo enviado a Ideas. Complétalo y pásalo por la evaluación de la IA.")
    return ir("/ideas?filtro=mias")


@router.get("/voz-cliente")
def voz_cliente(request: Request, tipo: str = "", semanas: int = 12, db: Session = Depends(get_db),
                u: Usuario = Depends(requiere_lider)):
    semanas = max(4, min(semanas, 52))
    r = vozcliente.resumen(db, semanas, tipo or None)
    evid: dict[int, list[int]] = {}
    for s in r["sesiones"]:
        if s.compromiso:
            d = evid.setdefault(s.compromiso.responsable_id, [0, 0])
            d[1] += 1
            d[0] += 1 if s.evidencia_verificada else 0
    nombres = {x.id: x for x in db.scalars(select(Usuario))}
    return render(request, "voz_cliente.html", u, "voz", r=r, tipo=tipo, semanas=semanas, evid=evid, nombres=nombres,
                  cuadrantes=CUADRANTES)

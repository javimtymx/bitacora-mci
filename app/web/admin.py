"""Administración: usuarios (alta y baja), parámetros con historial y catálogo estratégico versionado."""
from __future__ import annotations

import secrets
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    ROLES,
    Area,
    Compromiso,
    Estrategia,
    Mci,
    ParametroHistorial,
    Predictiva,
    Usuario,
    utcnow,
)
from ..services import catalogo, cfdi, correo, parametros, ventas_auto
from ..services.util import auditar
from ..config import get_settings
from ..services import accesos
from .base import flash, hash_password, ir, render, requiere_direccion, OptInt, OptFloat, OptDate

router = APIRouter(prefix="/admin")


def _areas(db: Session) -> list[Area]:
    return list(db.scalars(select(Area).order_by(Area.nombre)))


# ---------------------------------------------------------------- usuarios

@router.get("/usuarios")
def usuarios(request: Request, ver: str = "activos", db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    q = select(Usuario).where(Usuario.activo.is_(ver == "activos"))
    return render(request, "admin_usuarios.html", u, "admin", lista=list(db.scalars(q.order_by(Usuario.nombre))),
                  areas=_areas(db), roles=ROLES, ver=ver, menus=accesos.MENUS, grupos=dict((g, t) for g, t, _ in accesos.GRUPOS),
                  por_rol=accesos.por_rol,
                  activos=list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre))))


@router.post("/usuarios")
def alta_usuario(request: Request, nombre: str = Form(...), email: str = Form(...), rol: str = Form("colaborador"),
                 area_id: OptInt = Form(None), puede_confidencial: bool = Form(False),
                 enviar_invitacion: bool = Form(False), db: Session = Depends(get_db),
                 u: Usuario = Depends(requiere_direccion)):
    email = email.strip().lower()
    if rol not in ROLES:
        raise HTTPException(400)
    if rol == "admin" and u.rol != "admin":
        raise HTTPException(403, "Solo un administrador puede crear administradores.")
    existente = db.scalar(select(Usuario).where(Usuario.email == email))
    if existente and existente.activo:
        flash(request, "Ya existe un usuario activo con ese correo.", "error")
        return ir("/admin/usuarios")
    temporal = secrets.token_urlsafe(9)
    if existente:  # reactivación: conserva su histórico
        nu = existente
        nu.activo, nu.baja_en, nu.baja_motivo = True, None, None
        nu.nombre, nu.rol, nu.area_id, nu.puede_confidencial = nombre.strip(), rol, area_id or None, puede_confidencial
        nu.password_hash = hash_password(temporal)
    else:
        nu = Usuario(nombre=nombre.strip(), email=email, rol=rol, area_id=area_id or None,
                     puede_confidencial=puede_confidencial, password_hash=hash_password(temporal))
        db.add(nu)
    db.flush()
    auditar(db, "alta", "usuario", nu.id, u.id, rol=rol, confidencial=puede_confidencial)
    db.commit()
    if enviar_invitacion:
        s = get_settings()
        correo.enviar([email], "Tu acceso a la Bitácora MCI",
                      f"Hola {nombre.split()[0]}:\n\nYa tienes acceso a la Bitácora MCI de OneCore.\n\n"
                      f"Usuario: {email}\nContraseña temporal: {temporal}\n\nCámbiala al entrar.", f"{s.base_url}/login")
        flash(request, f"Usuario creado y se envió la invitación a {email}.")
    else:
        flash(request, f"Usuario creado. Contraseña temporal: {temporal} (compártela por un canal seguro).")
    return ir("/admin/usuarios")


@router.post("/usuarios/{uid}")
def editar_usuario(uid: int, request: Request, nombre: str = Form(...), rol: str = Form(...),
                   area_id: OptInt = Form(None), puede_confidencial: bool = Form(False),
                   recibe_correos: bool = Form(False), menus: list[str] = Form([]), menus_form: str = Form(""),
                   db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    x = db.get(Usuario, uid)
    if not x or rol not in ROLES:
        raise HTTPException(400)
    if (rol == "admin" or x.rol == "admin") and u.rol != "admin":
        raise HTTPException(403)
    cambios = {"rol": (x.rol, rol), "confidencial": (x.puede_confidencial, puede_confidencial)}
    x.nombre, x.rol, x.area_id = nombre.strip(), rol, area_id or None
    x.puede_confidencial, x.recibe_correos = puede_confidencial, recibe_correos
    if menus_form:
        bloq = sorted(m.clave for m in accesos.MENUS if not m.opt_in and accesos.por_rol(rol, m) and m.clave not in menus)
        extra = sorted(m.clave for m in accesos.MENUS if m.opt_in and m.clave in menus)
        cambios["menus_bloqueados"] = (sorted(x.menus_bloqueados or []), bloq)
        cambios["menus_otorgados"] = (sorted(x.menus_extra or []), extra)
        x.menus_bloqueados = bloq or None
        x.menus_extra = extra or None
    auditar(db, "editar", "usuario", x.id, u.id, **{k: list(v) for k, v in cambios.items() if v[0] != v[1]})
    db.commit()
    flash(request, "Usuario actualizado.")
    return ir("/admin/usuarios")


@router.post("/usuarios/{uid}/password")
def reset_password(uid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    x = db.get(Usuario, uid)
    if not x:
        raise HTTPException(404)
    temporal = secrets.token_urlsafe(9)
    x.password_hash = hash_password(temporal)
    auditar(db, "reset_password", "usuario", x.id, u.id)
    db.commit()
    flash(request, f"Nueva contraseña temporal para {x.nombre}: {temporal}")
    return ir("/admin/usuarios")


@router.post("/usuarios/{uid}/baja")
def baja_usuario(uid: int, request: Request, motivo: str = Form(...), reasignar_a: OptInt = Form(None),
                 db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    """Baja lógica: el histórico se conserva; los compromisos abiertos se reasignan o se cancelan."""
    x = db.get(Usuario, uid)
    if not x or x.id == u.id:
        raise HTTPException(400, "No puedes darte de baja a ti mismo.")
    abiertos = list(db.scalars(select(Compromiso).where(Compromiso.responsable_id == x.id, Compromiso.estado == "abierto")))
    destino = db.get(Usuario, reasignar_a) if reasignar_a else None
    for c in abiertos:
        if destino and destino.activo:
            c.responsable_id = destino.id
        else:
            c.estado, c.cerrado = "cancelado", utcnow()
    for est in db.scalars(select(Estrategia).where(Estrategia.responsable_id == x.id)):
        est.responsable_id = destino.id if destino else None
    x.activo, x.baja_en, x.baja_motivo = False, utcnow(), motivo.strip()
    x.password_hash = hash_password(secrets.token_urlsafe(24))
    auditar(db, "baja", "usuario", x.id, u.id, motivo=motivo, compromisos=len(abiertos),
            reasignados_a=destino.id if destino else None)
    db.commit()
    flash(request, f"{x.nombre} dado de baja. {len(abiertos)} compromisos abiertos "
                   f"{'reasignados a ' + destino.nombre if destino else 'cancelados'}; su histórico se conserva.")
    return ir("/admin/usuarios")


# ---------------------------------------------------------------- parámetros

@router.get("/parametros")
def ver_parametros(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    hist = list(db.scalars(select(ParametroHistorial).order_by(ParametroHistorial.creado.desc()).limit(50)))
    nombres = {x.id: x.nombre for x in db.scalars(select(Usuario))}
    return render(request, "admin_parametros.html", u, "admin", params=parametros.todos(db), hist=hist, nombres=nombres)


@router.post("/parametros")
async def guardar_parametros(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    form = await request.form()
    motivo = str(form.get("motivo") or "").strip()
    if not motivo:
        flash(request, "Indica el motivo del cambio; queda en el historial.", "error")
        return ir("/admin/parametros")
    n = 0
    for p in parametros.todos(db):
        k = p["clave"]
        if isinstance(p["default"], bool):
            nuevo = k in form
        elif k in form:
            try:
                nuevo = int(str(form[k]))
            except ValueError:
                continue
        else:
            continue
        if nuevo != p["valor"]:
            parametros.set_(db, k, nuevo, u.id, motivo)
            n += 1
    if n:
        auditar(db, "parametros", "sistema", None, u.id, cambios=n, motivo=motivo)
    db.commit()
    flash(request, f"{n} parámetro(s) actualizados." if n else "Sin cambios.")
    return ir("/admin/parametros")


# ---------------------------------------------------------------- catálogo estratégico

@router.get("/catalogo")
def ver_catalogo(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    mcis = list(db.scalars(select(Mci).order_by(Mci.area_id, Mci.nombre)))
    datos = ventas_auto.Datos(db)
    resumenes = {m.id: ventas_auto.resumen(db, m, datos) for m in mcis}
    return render(request, "admin_catalogo.html", u, "admin", mcis=mcis, areas=_areas(db), resumenes=resumenes,
                  fuentes=ventas_auto.FUENTES,
                  usuarios=list(db.scalars(select(Usuario).where(Usuario.activo.is_(True)).order_by(Usuario.nombre))),
                  vigente=catalogo.version_vigente)


@router.post("/areas")
def crear_area(request: Request, nombre: str = Form(...), clave: str = Form(...), db: Session = Depends(get_db),
               u: Usuario = Depends(requiere_direccion)):
    a = Area(nombre=nombre.strip(), clave=clave.strip().lower())
    db.add(a)
    db.flush()
    auditar(db, "crear", "area", a.id, u.id)
    db.commit()
    return ir("/admin/catalogo")


@router.post("/mci")
def crear_mci(request: Request, area_id: int = Form(...), clave: str = Form(...), nombre: str = Form(...),
              descripcion: str = Form(...), meta: OptFloat = Form(None), unidad: str = Form(""),
              sentido: str = Form("mayor"), periodicidad: str = Form("mensual"), vigente_desde: date = Form(...),
              tipo_meta: str = Form("nivel"), linea_base: OptFloat = Form(None), fecha_meta: OptDate = Form(None),
              fuente: str = Form("captura"), base_desde: str = Form(""), base_hasta: str = Form(""),
              dueno_id: OptInt = Form(None), db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    m = Mci(area_id=area_id, clave=clave.strip(), nombre=nombre.strip(), dueno_id=dueno_id or None)
    db.add(m)
    db.flush()
    catalogo.nueva_version_mci(db, m, descripcion=descripcion, meta=meta, unidad=unidad, sentido=sentido,
                               periodicidad=periodicidad, vigente_desde=vigente_desde, motivo="Alta", usuario_id=u.id,
                               tipo_meta=tipo_meta, linea_base=linea_base, fecha_meta=fecha_meta,
                               fuente=fuente, base_desde=base_desde.strip() or None, base_hasta=base_hasta.strip() or None)
    db.commit()
    ventas_auto.medir(db)
    flash(request, "MCI creada.")
    return ir("/admin/catalogo")


@router.post("/mci/{mid}/version")
def version_mci(mid: int, request: Request, descripcion: str = Form(...), meta: OptFloat = Form(None),
                unidad: str = Form(""), sentido: str = Form("mayor"), periodicidad: str = Form("mensual"),
                vigente_desde: date = Form(...), motivo: str = Form(...), tipo_meta: str = Form("nivel"),
                linea_base: OptFloat = Form(None), fecha_meta: OptDate = Form(None),
                fuente: str = Form("captura"), base_desde: str = Form(""), base_hasta: str = Form(""),
                db: Session = Depends(get_db),
                u: Usuario = Depends(requiere_direccion)):
    m = db.get(Mci, mid)
    if not m or not motivo.strip():
        raise HTTPException(400, "El cambio de meta requiere motivo.")
    if m.versiones and vigente_desde <= m.versiones[-1].vigente_desde:
        raise HTTPException(400, "La nueva versión debe iniciar después de la vigente.")
    catalogo.nueva_version_mci(db, m, descripcion=descripcion, meta=meta, unidad=unidad, sentido=sentido,
                               periodicidad=periodicidad, vigente_desde=vigente_desde, motivo=motivo, usuario_id=u.id,
                               tipo_meta=tipo_meta, linea_base=linea_base, fecha_meta=fecha_meta,
                               fuente=fuente, base_desde=base_desde.strip() or None, base_hasta=base_hasta.strip() or None)
    db.commit()
    ventas_auto.medir(db)
    flash(request, "Nueva versión de la MCI registrada; el histórico conserva la meta anterior.")
    return ir("/admin/catalogo")


@router.post("/predictivas")
def crear_predictiva(request: Request, mci_id: int = Form(...), clave: str = Form(...), nombre: str = Form(...),
                     descripcion: str = Form(...), meta_semanal: OptFloat = Form(None), unidad: str = Form(""),
                     sentido: str = Form("mayor"), fuente: str = Form("captura"), vigente_desde: date = Form(...),
                     db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    p = Predictiva(mci_id=mci_id, clave=clave.strip(), nombre=nombre.strip())
    db.add(p)
    db.flush()
    catalogo.nueva_version_predictiva(db, p, descripcion=descripcion, meta_semanal=meta_semanal, unidad=unidad,
                                      sentido=sentido, fuente=fuente, vigente_desde=vigente_desde, motivo="Alta",
                                      usuario_id=u.id)
    db.commit()
    flash(request, "Medida predictiva creada.")
    return ir("/admin/catalogo")


@router.post("/predictivas/{pid}/version")
def version_predictiva(pid: int, request: Request, descripcion: str = Form(...), meta_semanal: OptFloat = Form(None),
                       unidad: str = Form(""), sentido: str = Form("mayor"), fuente: str = Form("captura"),
                       vigente_desde: date = Form(...), motivo: str = Form(...), db: Session = Depends(get_db),
                       u: Usuario = Depends(requiere_direccion)):
    p = db.get(Predictiva, pid)
    if not p or not motivo.strip():
        raise HTTPException(400, "El cambio requiere motivo.")
    if p.versiones and vigente_desde <= p.versiones[-1].vigente_desde:
        raise HTTPException(400, "La nueva versión debe iniciar después de la vigente.")
    catalogo.nueva_version_predictiva(db, p, descripcion=descripcion, meta_semanal=meta_semanal, unidad=unidad,
                                      sentido=sentido, fuente=fuente, vigente_desde=vigente_desde, motivo=motivo,
                                      usuario_id=u.id)
    db.commit()
    flash(request, "Nueva versión de la medida registrada.")
    return ir("/admin/catalogo")


@router.post("/predictivas/{pid}/activo")
def activo_predictiva(pid: int, request: Request, motivo: str = Form(...), db: Session = Depends(get_db),
                      u: Usuario = Depends(requiere_direccion)):
    p = db.get(Predictiva, pid)
    if not p:
        raise HTTPException(404)
    p.activo = not p.activo
    auditar(db, "activar" if p.activo else "retirar", "predictiva", p.id, u.id, motivo=motivo)
    db.commit()
    return ir("/admin/catalogo")


@router.post("/predictivas/{pid}/evidencia")
def evidencia_predictiva(pid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    p = db.get(Predictiva, pid)
    if not p:
        raise HTTPException(404)
    p.requiere_evidencia = not p.requiere_evidencia
    auditar(db, "evidencia", "predictiva", p.id, u.id, requiere=p.requiere_evidencia)
    db.commit()
    return ir("/admin/catalogo")


@router.post("/cfdi/sincronizar")
def sync_cfdi(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    try:
        n = cfdi.sincronizar(db)
        flash(request, f"Ventas sincronizadas desde CFDI: {n} comprobantes leídos.")
    except Exception as e:  # noqa: BLE001
        flash(request, f"No se pudo leer la base de CFDI: {str(e)[:200]}", "error")
    return ir("/indicadores")

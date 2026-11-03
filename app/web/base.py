"""Utilidades comunes de la capa web: sesión, permisos, plantillas y mensajes."""
from __future__ import annotations
from datetime import date

from pydantic_core import core_schema

import html
import re
from pathlib import Path

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Compromiso, Usuario, utcnow
from ..services import util

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def md(texto: str | None) -> Markup:
    """Markdown mínimo y seguro: títulos, listas, negritas y párrafos."""
    if not texto:
        return Markup("")
    out, lista = [], False
    for linea in texto.splitlines():
        s = html.escape(linea.strip())
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        if s.startswith(("- ", "* ")):
            if not lista:
                out.append("<ul>")
                lista = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        if lista:
            out.append("</ul>")
            lista = False
        if not s:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            n = len(m.group(1)) + 1
            out.append(f"<h{n}>{m.group(2)}</h{n}>")
        else:
            out.append(f"<p>{s}</p>")
    if lista:
        out.append("</ul>")
    return Markup("".join(out))


def valor(v, unidad: str = "", dec: int = 1, signo: bool = False) -> str:
    """Número con su unidad: 21% · 9 mejoras · 99.968% · meta sin ceros de más."""
    if v is None:
        return "—"
    v = float(v)
    txt = f"{v:+,.{dec}f}" if signo else f"{v:,.{dec}f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    if signo and txt in ("+0", "-0"):
        txt = "0"
    if not unidad:
        return txt
    return f"{txt}{unidad}" if unidad.startswith("%") else f"{txt} {unidad}"


from ..services import accesos  # noqa: E402

TEMPLATES.env.globals.update(fmt_fecha=util.fmt_fecha, fmt_dia=util.fmt_dia, dias_desde=util.dias_desde,
                             num_semana=util.num_semana, md=md, valor=valor, menu_lateral=accesos.menu_lateral,
                             puede_menu=accesos.puede)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


class NoAutenticado(Exception):
    pass


class Redirigir(Exception):
    def __init__(self, url: str):
        self.url = url


def usuario_actual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    uid = request.session.get("uid")
    u = db.get(Usuario, uid) if uid else None
    if not u or not u.activo:
        request.session.clear()
        raise NoAutenticado()
    clave = accesos.menu_de_ruta(request.url.path)
    if clave and not accesos.puede(u, clave):
        if request.url.path == "/" and request.method == "GET":
            raise Redirigir(accesos.primera_pagina(u))
        raise HTTPException(403, "No tienes acceso a esta sección. Pídelo a Dirección.")
    return u


def requiere_lider(u: Usuario = Depends(usuario_actual)) -> Usuario:
    if not u.es_lider:
        raise HTTPException(403, "Necesitas rol de líder o Dirección.")
    return u


def requiere_direccion(u: Usuario = Depends(usuario_actual)) -> Usuario:
    if not u.es_direccion:
        raise HTTPException(403, "Solo Dirección puede hacer esto.")
    return u


def puede_actuar(u: Usuario, c: Compromiso) -> bool:
    if u.es_direccion or c.responsable_id == u.id or c.autor_id == u.id:
        return True
    return u.rol == "lider" and u.area_id == c.area_id


def flash(request: Request, texto: str, tipo: str = "ok") -> None:
    request.session.setdefault("flash", []).append({"t": texto, "k": tipo})


def render(request: Request, plantilla: str, u: Usuario | None, nav: str = "", **ctx):
    msgs = request.session.pop("flash", []) if "flash" in request.session else []
    return TEMPLATES.TemplateResponse(request, plantilla, {"u": u, "nav": nav, "flashes": msgs,
                                                           "hoy": util.hoy(), **ctx})


def ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def tocar_acceso(db: Session, u: Usuario) -> None:
    u.ultimo_acceso = utcnow()


# Los formularios HTML mandan "" cuando un select opcional queda en "Ninguna" o un número se deja vacío;
# estos tipos lo convierten en None en lugar de rechazar el formulario.
class _Opcional:
    tipo: type = int

    @classmethod
    def __get_pydantic_core_schema__(cls, origen, handler):
        return core_schema.no_info_before_validator_function(
            lambda v: None if isinstance(v, str) and not v.strip() else v,
            core_schema.nullable_schema(handler.generate_schema(cls.tipo)))


class OptInt(_Opcional):
    tipo = int


class OptFloat(_Opcional):
    tipo = float


class OptDate(_Opcional):
    tipo = date

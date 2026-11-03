"""Aplicación FastAPI de la Bitácora MCI."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .web import admin, captura, gestion, reportes
from .web.base import NoAutenticado, TEMPLATES, ir

s = get_settings()
if s.cookie_secure and (len(s.secret_key) < 32 or s.secret_key.startswith(("dev-", "CAMBIA"))):
    raise RuntimeError("Configura SECRET_KEY en .env antes de arrancar en producción.")
app = FastAPI(title="Bitácora MCI · OneCore", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=s.secret_key, https_only=s.cookie_secure, same_site="lax",
                   max_age=60 * 60 * 12, session_cookie="bitacora")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

for r in (captura.router, gestion.router, admin.router, reportes.router):
    app.include_router(r)


@app.exception_handler(NoAutenticado)
async def _no_auth(request: Request, exc: NoAutenticado):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "mensaje": "Tu sesión expiró. Vuelve a entrar."}, status_code=401)
    return ir("/login")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "mensaje": exc.detail}, status_code=exc.status_code)
    titulos = {403: "Sin permiso", 404: "No encontrado", 400: "Revisa los datos"}
    return TEMPLATES.TemplateResponse(request, "error.html", {
        "u": None, "nav": "", "flashes": [], "codigo": exc.status_code,
        "titulo": titulos.get(exc.status_code, "Algo salió mal"),
        "detalle": exc.detail if isinstance(exc.detail, str) else ""}, status_code=exc.status_code)


@app.get("/salud")
def salud():
    return {"ok": True}

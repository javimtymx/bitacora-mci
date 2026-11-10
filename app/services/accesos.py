"""Menús de la aplicación y acceso por usuario.

Una sola definición alimenta la barra lateral, la pantalla de Usuarios y el bloqueo en el servidor:
si un menú está bloqueado para alguien, tampoco puede entrar escribiendo la dirección.
El rol pone el techo (un colaborador nunca ve menús de líder) y Dirección puede quitar menús a cada persona.
Los administradores siempre ven todo, para que nadie se quede fuera de Configurar.
Algunos menús son «por otorgamiento» (opt_in): nadie los ve por su rol; Dirección se los da a cada persona
(por ejemplo, el tablero de desarrollo de tickets solo a quien atiende tickets).
"""
from __future__ import annotations

from dataclasses import dataclass

NIVEL = {"colaborador": 0, "lider": 1, "direccion": 2, "admin": 3}


@dataclass(frozen=True)
class Menu:
    clave: str
    grupo: str
    titulo: str
    desc: str
    href: str
    rol_min: str  # colaborador | lider | direccion
    rutas: tuple[str, ...]
    opt_in: bool = False  # solo lo ve quien lo tiene otorgado (además de los administradores)


GRUPOS = (("tablero", "Tablero", "home"), ("ejecucion", "Ejecución", "check"), ("ia", "GuardIA", "ia"),
          ("innovacion", "Innovación", "idea"), ("soporte", "Soporte", "ticket"), ("datos", "Datos", "chart"))

MENUS = (
    Menu("inicio", "tablero", "Tablero", "Resultado del mes y seguimiento semanal", "/", "colaborador", ("/kardex",)),
    Menu("mi-semana", "tablero", "Mi semana", "Mis compromisos de esta semana", "/mi-semana", "colaborador", ("/mi-semana",)),
    Menu("compromisos", "ejecucion", "Compromisos", "Seguimiento y remediaciones", "/compromisos", "colaborador",
         ("/compromisos", "/api/ia/compromiso", "/api/ia/remediacion", "/remediaciones", "/evidencias")),
    Menu("semanal", "ejecucion", "Junta semanal", "Vista para presentar", "/semanal", "lider", ("/semanal",)),
    Menu("excepciones", "ejecucion", "Excepciones", "Solicitudes y aprobaciones", "/excepciones", "lider", ("/excepciones",)),
    Menu("diagnostico", "ia", "Diagnóstico por área", "Recomendaciones semanales", "/diagnostico", "lider",
         ("/diagnostico", "/recomendaciones")),
    Menu("pm", "ia", "GuardIA OneCore", "Informe, alertas y documentos", "/pm", "lider", ("/pm", "/acciones", "/informes")),
    Menu("voz", "ia", "Voz del cliente", "Sesiones 1:1 y objeciones", "/voz-cliente", "lider",
         ("/voz-cliente", "/sesiones", "/hallazgos")),
    Menu("ideas", "innovacion", "Ideas", "Propuestas del equipo", "/ideas", "colaborador", ("/ideas", "/api/ia/idea")),
    Menu("circulo", "innovacion", "Círculo de innovación", "Iniciativas y votación", "/circulo", "colaborador",
         ("/circulo", "/estrategias")),
    Menu("tickets", "soporte", "Tickets", "Levantar un ticket y ver los míos", "/tickets", "colaborador",
         ("/tickets", "/api/tickets", "/anexos-ticket")),
    Menu("tickets-triage", "soporte", "Triage", "Prioridad y responsable de lo nuevo", "/tickets-triage", "colaborador",
         ("/tickets-triage",), opt_in=True),
    Menu("tickets-tablero", "soporte", "Tablero de desarrollo", "Kanban de tickets en atención", "/tickets-tablero", "colaborador",
         ("/tickets-tablero",), opt_in=True),
    Menu("tickets-analitica", "soporte", "Analítica de tickets", "Indicadores y diagnóstico por área", "/tickets-analitica",
         "direccion", ("/tickets-analitica",), opt_in=True),
    Menu("tickets-config", "configurar", "Configurar GuardIA Ticket", "Qué pide GuardIA, catálogo y reglas", "/tickets-config",
         "colaborador", ("/tickets-config",), opt_in=True),
    Menu("indicadores", "datos", "Indicadores", "Kárdex y captura semanal", "/indicadores", "colaborador", ("/indicadores",)),
    Menu("reportes", "datos", "Reportes", "Descargas en Excel", "/reportes", "colaborador", ("/reportes",)),
    Menu("socio", "datos", "Vista socio", "Resumen ejecutivo", "/socio", "direccion", ("/socio",)),
)
POR_CLAVE = {m.clave: m for m in MENUS}


def _rutas():
    out = [("/", "inicio", True)]
    for m in MENUS:
        out += [(r, m.clave, False) for r in m.rutas]
    return sorted(out, key=lambda x: -len(x[0]))


_RUTAS = _rutas()


def menu_de_ruta(path: str) -> str | None:
    for pref, clave, exacto in _RUTAS:
        if (exacto and path == pref) or (not exacto and (path == pref or path.startswith(pref + "/") or
                                                          path.startswith(pref + "."))):
            return clave
    return None


def por_rol(rol: str, m: Menu) -> bool:
    return NIVEL.get(rol, 0) >= NIVEL[m.rol_min]


def puede(u, clave: str) -> bool:
    m = POR_CLAVE.get(clave)
    if m is None or u is None:
        return u is not None
    if u.rol == "admin":
        return True
    if m.opt_in:  # se otorga a cada persona; si el menú pide un rol (p. ej. Dirección), ese rol lo ve sin otorgarlo
        return clave in (getattr(u, "menus_extra", None) or []) or (m.rol_min != "colaborador" and por_rol(u.rol, m))
    return por_rol(u.rol, m) and clave not in (u.menus_bloqueados or [])


def menu_lateral(u) -> list[tuple[str, str, str, list[Menu]]]:
    out = []
    for g, titulo, icono in GRUPOS:
        items = [m for m in MENUS if m.grupo == g and puede(u, m.clave)]
        if items:
            out.append((g, titulo, icono, items))
    return out


def primera_pagina(u) -> str:
    for m in MENUS:
        if puede(u, m.clave):
            return m.href
    return "/perfil"

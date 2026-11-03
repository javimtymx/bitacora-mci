"""PM virtual: tareas programadas.

- informe_diario: alertas + informe de la IA + propuestas de recordatorio.
- recordatorios: remediaciones que vencen mañana, personas sin compromiso, seguimiento de estrategias.
- Las acciones se ejecutan con ejecutar_pendientes (servicio acciones)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Compromiso, Estrategia, Informe, Remediacion, VentaMensual, utcnow
from . import acciones, ia, parametros, seguimiento
from .util import hoy, semana_iso


def _contexto(db: Session) -> tuple[str, dict[str, int]]:
    personas = seguimiento.estado_personas(db)
    codigos: dict[str, int] = {}
    lineas = []
    for i, e in enumerate(personas, 1):
        codigos[f"P{i}"] = e.usuario.id
        pct = f"{e.cumpl.pct:.0f}%" if e.cumpl.pct is not None else "s/d"
        lineas.append(f"P{i} | {e.usuario.nombre} | {e.usuario.area.nombre if e.usuario.area else '—'} | "
                      f"compromisos semana {e.semana} | cumplimiento 4 sem {pct} | vencidos {e.vencidos} | "
                      f"sin remediar {e.sin_remediar} | días sin captura {e.dias_sin_captura if e.dias_sin_captura is not None else 'nunca'} | "
                      f"ideas del mes {e.ideas_mes} (débiles {e.ideas_debiles}) | estado {e.estado}")
    al = seguimiento.alertas(db)[:40]
    inv = {v: k for k, v in codigos.items()}
    lal = [f"- [{a.severidad}] {inv.get(a.usuario.id, '') if a.usuario else 'Área'} {a.area_nombre}: {a.texto}" for a in al]
    ests = []
    for est in db.scalars(select(Estrategia).where(Estrategia.estado == "activa")):
        st = seguimiento.stats_estrategia(db, est)
        pct = f"{st['pct']:.0f}%" if st["pct"] is not None else "s/d"
        ests.append(f"- {est.area.nombre} · {est.nombre}: {st['compromisos']} compromisos, cumplimiento {pct}")
    ventas = list(db.scalars(select(VentaMensual).order_by(VentaMensual.periodo.desc()).limit(3)))
    lv = [f"- {v.periodo}: {v.clientes} clientes, {v.clientes_nuevos} nuevos" for v in ventas]
    ctx = (f"Hoy: {hoy():%Y-%m-%d} (semana {semana_iso(hoy())})\n\nPersonas (código | nombre | área | datos):\n"
           + ("\n".join(lineas) or "- sin personas")
           + "\n\nAlertas:\n" + ("\n".join(lal) or "- sin alertas")
           + "\n\nEstrategias activas:\n" + ("\n".join(ests) or "- ninguna")
           + "\n\nVentas recientes (desde CFDI):\n" + ("\n".join(lv) or "- sin datos"))
    return ctx, codigos


def informe_diario(db: Session) -> Informe:
    ctx, codigos = _contexto(db)
    al = seguimiento.alertas(db)
    try:
        res, meta = ia.informe_pm(db, ctx)
        resumen = str(res.get("resumen") or "")
        acc = []
        for a in (res.get("acciones") or [])[:10]:
            if isinstance(a, dict):
                acc.append({"usuario_id": codigos.get(str(a.get("persona") or "").strip()),
                            "area": str(a.get("area") or ""), "accion": str(a.get("accion") or "")[:300],
                            "prioridad": a.get("prioridad") if a.get("prioridad") in ("alta", "media", "baja") else "media"})
        riesgos = [str(r)[:300] for r in (res.get("riesgos") or [])][:3]
        modelo = meta.get("modelo")
    except (ia.IANoDisponible, ValueError):
        resumen = f"Informe sin IA: {len(al)} alertas activas, {sum(1 for a in al if a.severidad == 3)} urgentes."
        acc, riesgos, modelo = [], [], None
    md = resumen + "\n\n" + "\n".join(f"- **{a['prioridad']}** · {a['area']}: {a['accion']}" for a in acc)
    if riesgos:
        md += "\n\n**Riesgos**\n" + "\n".join(f"- {r}" for r in riesgos)
    inf = Informe(tipo="pm_diario", titulo=f"Informe del PM · {hoy():%d/%m/%Y}", periodo=semana_iso(hoy()),
                  contenido=md, datos={"resumen": resumen, "acciones": acc, "riesgos": riesgos,
                                       "alertas": len(al), "modelo": modelo}, origen="agente")
    db.add(inf)
    db.commit()
    return inf


def recordatorios(db: Session) -> int:
    n = 0
    ayer = utcnow() - timedelta(days=1)
    manana = hoy() + timedelta(days=1)
    # 1) remediaciones que vencen mañana
    auto = parametros.get(db, "auto_recordatorio_remediacion")
    for r in db.scalars(select(Remediacion).where(Remediacion.estado == "abierta", Remediacion.nueva_fecha == manana)):
        c = r.compromiso
        if acciones.ya_existe(db, "recordatorio_remediacion", "compromiso", c.id, ayer):
            continue
        acciones.crear(db, tipo="recordatorio", origen="recordatorio_remediacion",
                       titulo=f"Remediación vence mañana: {c.texto[:80]}", destinatarios=[c.responsable_id],
                       asunto="Tu remediación vence mañana",
                       cuerpo=(f"Hola {c.responsable.nombre.split()[0]}:\n\nMañana vence la remediación que acordaste: "
                               f"{r.accion}\n\nSi ya la cumpliste, márcala en la bitácora. Si no la vas a lograr, "
                               f"avisa hoy a tu líder."),
                       entidad_tipo="compromiso", entidad_id=c.id, auto=auto)
        n += 1
    # 2) personas sin compromiso esta semana (desde el martes)
    if hoy().weekday() >= 1:
        auto = parametros.get(db, "auto_recordatorio_sin_compromiso")
        for e in seguimiento.estado_personas(db):
            if e.semana == 0 and e.usuario.recibe_correos:
                if acciones.ya_existe(db, "sin_compromiso", "usuario", e.usuario.id, ayer):
                    continue
                acciones.crear(db, tipo="recordatorio", origen="sin_compromiso",
                               titulo=f"Sin compromiso esta semana: {e.usuario.nombre}", destinatarios=[e.usuario.id],
                               asunto="Registra tu compromiso de la semana",
                               cuerpo=(f"Hola {e.usuario.nombre.split()[0]}:\n\nAún no registras tu compromiso de la semana "
                                       f"{semana_iso(hoy())[-2:]}. Elige una acción concreta que mueva tu medida predictiva "
                                       f"y regístrala; el PM virtual te ayuda a afinarla."),
                               entidad_tipo="usuario", entidad_id=e.usuario.id, auto=auto)
                n += 1
    # 3) seguimiento semanal de estrategias activas (lunes)
    if hoy().weekday() == 0:
        auto = parametros.get(db, "auto_seguimiento_estrategias")
        hace6 = utcnow() - timedelta(days=6)
        for est in db.scalars(select(Estrategia).where(Estrategia.estado == "activa", Estrategia.seguimiento == "semanal")):
            if not est.responsable_id or acciones.ya_existe(db, "estrategia", "estrategia", est.id, hace6):
                continue
            st = seguimiento.stats_estrategia(db, est)
            pct = f"{st['pct']:.0f}%" if st["pct"] is not None else "sin datos aún"
            acciones.crear(db, tipo="correo", origen="estrategia", titulo=f"Seguimiento semanal: {est.nombre}",
                           destinatarios=[est.responsable_id], asunto=f"Seguimiento de la estrategia «{est.nombre}»",
                           cuerpo=(f"Hola {est.responsable.nombre.split()[0]}:\n\nLa estrategia «{est.nombre}» que se acordó "
                                   f"sigue activa. Lleva {st['compromisos']} compromisos ligados con cumplimiento de {pct}.\n\n"
                                   f"Registra esta semana el compromiso que la hace avanzar, o propone en la junta "
                                   f"si conviene ajustarla."),
                           entidad_tipo="estrategia", entidad_id=est.id, auto=auto)
            n += 1
    db.commit()
    return n

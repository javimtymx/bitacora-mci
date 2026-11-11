"""Recurrencias de servicio: análisis compilado de lo que los clientes nos piden y reclaman en las sesiones.

Fuentes (todas de la agenda de clientes, bucle interno de seguimiento; no compite con Voz del Cliente):
- Pendientes de clientes (quejas y tareas), incluso lo que quedó en histórico.
- Minutas: acuerdos de OneCore sin tarea, riesgos o bloqueos, temas abiertos y puntos de agenda que no se cubrieron.
GuardIA agrupa lo que se repite en temas, propone causa raíz, impacto y un plan de acción. La frecuencia (cuántos clientes
distintos) la calcula el sistema, no la IA. Cuadrante frecuencia × impacto:
- Atacar ya: muchos clientes y alto impacto.
- Falla crónica: muchos clientes, impacto menor → estandarizar o automatizar.
- Riesgo puntual: pocos clientes, alto impacto → protocolo y atención caso por caso.
- Monitorear: pocos clientes y bajo impacto.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgendaCliente, AnalisisServicio, PendienteCliente, Usuario, utcnow
from . import ia
from .util import auditar

log = logging.getLogger("bitacora.servicio")

CUADRANTES = {
    "atacar": ("Atacar ya", "Muchos clientes y alto impacto: plan con responsable y fecha esta misma semana.", "r"),
    "cronica": ("Falla crónica", "Se repite en muchos clientes con impacto menor: estandarizar, automatizar o capacitar.", "o"),
    "puntual": ("Riesgo puntual", "Pocos clientes pero alto impacto: protocolo y atención caso por caso.", "p"),
    "monitorear": ("Monitorear", "Pocos clientes y bajo impacto: vigilar que no crezca.", "n"),
}
IMPACTO_ALTO = 4  # en escala 1-5


def _cliente(x) -> str:
    return (x.cliente_rfc or x.cliente_nombre or "").strip().lower()


def fuentes(db: Session, desde: date, hasta: date) -> dict[str, dict]:
    """{código: {texto, tipo, cliente, url, ...}} de pendientes y minutas en el periodo."""
    ini, fin = datetime.combine(desde, time.min), datetime.combine(hasta, time.max)
    out: dict[str, dict] = {}
    for p in db.scalars(select(PendienteCliente).where(PendienteCliente.creado >= ini, PendienteCliente.creado <= fin)):
        dias = (p.resuelto_en - p.creado).days if p.resuelto_en else None
        out[f"P{p.id}"] = {"tipo": p.tipo, "texto": p.titulo + (f" — {p.detalle}" if p.detalle else ""), "cita": p.cita,
                           "severidad": p.severidad, "cliente": _cliente(p), "modulo": p.producto, "estado": p.estado,
                           "dias_resolucion": dias, "url": f"/clientes/pendientes/{p.id}", "folio": p.folio,
                           "vencido": bool(p.fecha_compromiso and p.estado in ("pendiente", "en_curso", "revision")
                                           and p.fecha_compromiso < date.today())}
    for a in db.scalars(select(AgendaCliente).where(AgendaCliente.fecha >= desde, AgendaCliente.fecha <= hasta)):
        ej = a.ejecucion or {}
        mi = ej.get("minuta") or {}
        base = {"cliente": _cliente(a), "url": f"/clientes/agenda/{a.id}", "folio": f"Sesión {a.fecha:%d/%m}"}
        k = 0
        for x in mi.get("acuerdos", []):
            if x.get("quien") == "onecore" and not x.get("pendiente_id"):
                k += 1
                out[f"M{a.id}-{k}"] = {**base, "tipo": "acuerdo", "texto": x.get("que", ""), "cita": x.get("cita", "")}
        for x in mi.get("riesgos", []):
            k += 1
            out[f"M{a.id}-{k}"] = {**base, "tipo": "riesgo", "texto": x.get("texto", ""), "cita": x.get("cita", "")}
        for x in mi.get("temas_abiertos", []):
            k += 1
            out[f"M{a.id}-{k}"] = {**base, "tipo": "tema_abierto", "texto": x.get("texto", ""), "cita": ""}
        for x in ej.get("puntos", []):
            if x.get("cubierto") == "no":
                k += 1
                out[f"M{a.id}-{k}"] = {**base, "tipo": "punto_no_cubierto", "texto": x.get("punto", ""), "cita": ""}
    return out


def _prompt(fs: dict[str, dict], modulos: list[str]) -> str:
    anon: dict[str, int] = {}
    for f in fs.values():
        anon.setdefault(f["cliente"], len(anon) + 1)
    lineas = "\n".join(f"- {c} | {f['tipo']} | {f.get('severidad', '')} | cliente {anon[f['cliente']]:03d}"
                       f" | {f.get('modulo') or '-'} | {f['texto'][:220]}" for c, f in list(fs.items())[:400])
    return f"""Eres el analista de calidad de servicio de OneCore (software de cumplimiento aduanero para IMMEX, importadores
y agentes aduanales en México). Abajo están las quejas, tareas, acuerdos sin tarea, riesgos, temas abiertos y puntos no
cubiertos que salieron de las sesiones con clientes. Agrupa lo que SE REPITE (mismo problema de fondo aunque se diga
distinto) en temas de servicio. Para cada tema:
- titulo corto; categoria queja|tarea|riesgo|proceso; modulo (uno del catálogo o vacío).
- causa_raiz probable (aplica 5 porqués, una o dos frases; si no hay datos para saberla, dilo).
- impacto 1-5 y por qué: 5 = multas o incumplimiento ante SAT/ANAM, detiene la operación del cliente o riesgo de baja;
  3 = retrabajo o retraso relevante; 1 = molestia menor. NO consideres cuántos clientes lo dicen (eso se mide aparte).
- fuentes: los códigos de la lista que pertenecen al tema (todos los que apliquen).
- plan: 1 a 3 acciones concretas (empiezan con verbo) con tipo correctiva|preventiva|estandarizar, area responsable
  sugerida (Operaciones, Soporte/Sistemas, Producto, Customer Success, Comercial), plazo_dias e indicador para medir que
  funcionó.
Incluye también temas de un solo cliente si su impacto es 4 o 5. Máximo 15 temas.

<datos>
Catálogo de módulos: {", ".join(modulos) or "-"}
Fuentes (código | tipo | severidad | cliente anónimo | módulo | texto):
{lineas}
</datos>

JSON: {{"lectura":"3-4 frases: cómo estamos en servicio y qué atacar primero","temas":[{{"titulo":"...","categoria":"queja",
"modulo":"","causa_raiz":"...","impacto":4,"impacto_por":"...","fuentes":["P12","M3-1"],
"plan":[{{"accion":"...","tipo":"correctiva","area":"Operaciones","plazo_dias":15,"indicador":"..."}}]}}]}}"""


def analizar(db: Session, an: AnalisisServicio) -> AnalisisServicio:
    from .tickets import productos
    fs = fuentes(db, an.desde, an.hasta)
    if not fs:
        an.estado, an.resultado = "listo", {"lectura": "No hay quejas, tareas ni minutas en el periodo.", "temas": [], "n_fuentes": 0}
        db.commit()
        return an
    res, meta = ia.cliente().pedir_json(_prompt(fs, productos(db)), max_tokens=8000)
    temas = []
    for t in (res.get("temas") or [])[:15]:
        if not isinstance(t, dict) or not t.get("titulo"):
            continue
        cods = [c for c in dict.fromkeys(str(x).strip().upper() for x in (t.get("fuentes") or [])) if c in fs]
        if not cods:
            continue
        clientes = {fs[c]["cliente"] for c in cods if fs[c]["cliente"]}
        try:
            imp = max(1, min(5, int(round(float(t.get("impacto") or 3)))))
        except (TypeError, ValueError):
            imp = 3
        resueltos = [fs[c]["dias_resolucion"] for c in cods if fs[c].get("dias_resolucion") is not None]
        plan = [{"accion": str(x.get("accion"))[:300], "tipo": str(x.get("tipo") or "correctiva")[:15],
                 "area": str(x.get("area") or "")[:60], "plazo_dias": int(x.get("plazo_dias") or 0) if str(x.get("plazo_dias") or "0").isdigit() else 0,
                 "indicador": str(x.get("indicador") or "")[:200]} for x in (t.get("plan") or [])[:3] if isinstance(x, dict) and x.get("accion")]
        temas.append({"titulo": str(t["titulo"])[:160], "categoria": str(t.get("categoria") or "queja")[:12],
                      "modulo": str(t.get("modulo") or "")[:80], "causa_raiz": str(t.get("causa_raiz") or "")[:600],
                      "impacto": imp, "impacto_por": str(t.get("impacto_por") or "")[:300], "fuentes": cods,
                      "clientes": len(clientes) or 1, "menciones": len(cods),
                      "abiertos": sum(1 for c in cods if fs[c].get("estado") in ("propuesta", "pendiente", "en_curso", "revision", "delegado")),
                      "vencidos": sum(1 for c in cods if fs[c].get("vencido")),
                      "dias_resolucion": round(statistics.mean(resueltos), 1) if resueltos else None, "plan": plan})
    umbral = max(2, round(statistics.median([t["clientes"] for t in temas]))) if temas else 2
    for t in temas:
        alta_f, alto_i = t["clientes"] >= umbral, t["impacto"] >= IMPACTO_ALTO
        t["cuadrante"] = "atacar" if alta_f and alto_i else "cronica" if alta_f else "puntual" if alto_i else "monitorear"
    orden = {"atacar": 0, "puntual": 1, "cronica": 2, "monitorear": 3}
    temas.sort(key=lambda t: (orden[t["cuadrante"]], -t["impacto"] * t["clientes"]))
    an.resultado = {"lectura": str(res.get("lectura") or "")[:1500], "temas": temas, "umbral_clientes": umbral,
                    "n_fuentes": len(fs), "n_clientes": len({f["cliente"] for f in fs.values() if f["cliente"]}),
                    "fuentes": {c: {k: f.get(k) for k in ("tipo", "texto", "folio", "url", "estado")} for c, f in fs.items()
                                if any(c in t["fuentes"] for t in temas)},
                    "modelo": meta.get("modelo")}
    an.estado = "listo"
    auditar(db, "analizar", "analisis_servicio", an.id, an.autor_id, temas=len(temas))
    db.commit()
    return an


def analizar_en_fondo(aid: int) -> None:
    from ..db import SessionLocal
    with SessionLocal() as db:
        an = db.get(AnalisisServicio, aid)
        if not an:
            return
        try:
            analizar(db, an)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            an = db.get(AnalisisServicio, aid)
            an.estado = "error"
            an.resultado = {"error": str(e)[:300] if isinstance(e, (ia.IANoDisponible, ValueError))
                            else "Ocurrió un error inesperado al analizar. Intenta de nuevo."}
            db.commit()


def svg(temas: list[dict], umbral: int, ancho: int = 720, alto: int = 380) -> dict:
    """Coordenadas del cuadrante: X = clientes que lo repiten, Y = impacto (1-5)."""
    if not temas:
        return {}
    izq, der, arr, aba = 48, 20, 20, 44
    w, h = ancho - izq - der, alto - arr - aba
    xmax = max(max(t["clientes"] for t in temas), umbral * 2, 4)

    def x(v):
        return round(izq + (v / xmax) * w, 1)

    def y(v):
        return round(arr + h - (v - 0.5) / 5 * h, 1)
    puntos = []
    for i, t in enumerate(temas, 1):
        puntos.append({"n": i, "x": x(t["clientes"]), "y": y(t["impacto"]), "r": 9 + min(t["menciones"], 12),
                       "cuadrante": t["cuadrante"], "titulo": t["titulo"]})
    return {"ancho": ancho, "alto": alto, "izq": izq, "der": ancho - der, "arr": arr, "base": arr + h, "xu": x(umbral - 0.5),
            "yu": y(IMPACTO_ALTO - 0.5), "puntos": puntos, "xmax": xmax, "ticks_x": [(v, x(v)) for v in range(0, xmax + 1, max(1, xmax // 6))],
            "ticks_y": [(v, y(v)) for v in range(1, 6)]}

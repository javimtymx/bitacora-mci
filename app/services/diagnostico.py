"""Diagnóstico semanal de la IA por área, con especialista por área, foto semanal e investigación profunda.

Cada semana se guarda una foto (DiagnosticoSemanal): los datos que vio la IA, su conclusión y sus
recomendaciones. La semana siguiente la IA recibe esas recomendaciones con lo que pasó después
(compromisos creados a partir de ellas y movimiento del indicador) y evalúa si se ejecutaron y si
funcionaron, antes de recomendar otra vez."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Area,
    Compromiso,
    DiagnosticoSemanal,
    Estrategia,
    Hallazgo,
    Objecion,
    PromptArea,
    RecomendacionIA,
    SesionCliente,
    utcnow,
)
from . import ia, indicadores, parametros, seguimiento
from .util import auditar, hoy, semana_iso

PROMPT_VERSION = "2026.11-diag-1"

# Especialistas por defecto. Se cargan con `python -m app.cli seed` y se editan en Configurar → Prompts por área.
PROMPTS_DEFAULT = {
    "ventas": (
        "Director comercial experto en venta consultiva B2B de software regulatorio (SaaS) para comercio exterior",
        "Dominas MEDDICC, SPIN Selling, Challenger Sale, venta basada en cuentas (ABM), social selling, "
        "prospección por eventos y webinars, y expansión de cuentas (cross-selling). Lees el embudo: "
        "prospección, citas, cotizaciones y cierre. Distingues un problema de canal (el mensaje o el medio "
        "ya no funciona) de un problema de ejecución (no se hizo lo acordado). Conoces el ciclo de compra de "
        "importadores, empresas IMMEX y agentes aduanales en México: el comprador económico suele ser "
        "dirección de finanzas o el corporativo, y el usuario es comercio exterior."),
    "innovacion": (
        "Líder de innovación y mejora continua en empresas de software B2B",
        "Dominas Jobs-to-be-Done, design thinking, Lean Startup (experimentos y aprendizaje validado), "
        "kaizen, matrices de impacto y esfuerzo, y gestión de portafolio de ideas. Distingues una idea "
        "vaga de una con problema concreto y beneficio medible, y detectas ideas que nacen de cambios "
        "regulatorios (SAT, ANAM, RGCE, MVE) como oportunidades de producto."),
    "retencion": (
        "Director de Customer Success para SaaS B2B con contratos corporativos",
        "Dominas customer health score, señales tempranas de baja (uso, patrocinador interno, quejas, "
        "comparaciones de precio), QBR, playbooks de renovación, adopción por módulo y voz del cliente. "
        "Separas el churn por valor percibido del churn por servicio o por plataforma."),
    "operaciones": (
        "Gerente de operaciones aduanales y calidad, experto en validación de pedimentos",
        "Dominas cero defectos, Six Sigma, análisis de causa raíz (5 porqués, Ishikawa), controles "
        "preventivos antes del pago del pedimento, gestión de criterios de validación y su efectividad, "
        "y la operación diaria con VUCEM, COVE, Anexo 24/30/31, fracciones TIGIE/NICO."),
    "compliance": (
        "Especialista en cumplimiento de comercio exterior y analítica para clientes",
        "Dominas auditorías de comercio exterior, Anexo 24 y 31, activos fijos, certificaciones IVA/IEPS "
        "y OEA, y cómo traducir analíticos en hallazgos accionables para el cliente."),
    "direccion": (
        "Director de operaciones y transformación digital",
        "Dominas gestión de portafolio, asignación de capacidad, automatización de procesos (RPA e IA), "
        "casos de inversión, adopción de IA en equipos y las 4 Disciplinas de la Ejecución."),
}


def prompt_vigente(db: Session, area: Area) -> PromptArea | None:
    return db.scalar(select(PromptArea).where(PromptArea.area_id == area.id, PromptArea.activo.is_(True))
                     .order_by(PromptArea.version.desc()).limit(1))


def system_area(p: PromptArea | None) -> str:
    if not p:
        return ia.SYSTEM
    return (f"{ia.SYSTEM}\n\nEn este análisis actúas como: {p.especialidad}.\n{p.instrucciones}\n"
            "Usa métodos y técnicas vigentes de tu especialidad y nómbralos en cada recomendación. "
            "No inventes datos: si algo no está en <datos>, dilo.")


def investigaciones_usadas(db: Session, semana: str) -> int:
    return db.scalar(select(func.count(DiagnosticoSemanal.id)).where(
        DiagnosticoSemanal.semana == semana, DiagnosticoSemanal.investigacion.is_not(None))) or 0


def _contexto(db: Session, area: Area, semana: str) -> tuple[str, list[RecomendacionIA]]:
    sems = indicadores.semanas(8, semana)
    lineas = [f"Área: {area.nombre} · semana del diagnóstico: {semana}"]
    for m in indicadores.mcis_de(db, area.id):
        sr = indicadores.serie_mci(db, m, 8, semana)
        v = sr.version
        racha, n = sr.racha
        lineas.append(f"\nMCI «{m.nombre}» (meta {v.meta if v else '—'} {v.unidad if v else ''}, tipo {v.tipo_meta if v else ''}): "
                      f"{racha} {n} semana(s)")
        for p in sr.puntos:
            lineas.append(f"- {p.semana}: valor {indicadores.fmt(p.valor, 3)} · objetivo {indicadores.fmt(p.objetivo, 3)} · "
                          f"brecha {indicadores.fmt(p.brecha, 3)} · compromisos {p.cumplidos} cumplidos, "
                          f"{p.remediados} remediados, {p.en_remediacion} en remediación, {p.solo_texto} sin ejecutar")
        for sem, t in indicadores.eventos_mci(db, m, sems):
            lineas.append(f"  evento {sem}: {t}")
    comps = list(db.scalars(select(Compromiso).where(Compromiso.area_id == area.id, Compromiso.semana.in_(sems[-4:]))))
    causas: dict[str, int] = {}
    for c in comps:
        for r in c.remediaciones:
            causas[r.causa_categoria] = causas.get(r.causa_categoria, 0) + 1
    lineas.append(f"\nCausas de incumplimiento (4 semanas): {causas or 'ninguna'}")
    for c in comps[-25:]:
        r = c.remediacion
        lineas.append(f"- [{c.semana}] {c.texto[:140]} · {c.estado}" + (f" · causa: {r.causa[:100]} · remediación {r.estado}" if r else ""))
    ests = list(db.scalars(select(Estrategia).where(Estrategia.area_id == area.id, Estrategia.estado == "activa")))
    if ests:
        lineas.append("\nEstrategias activas:")
        for e in ests:
            st = seguimiento.stats_estrategia(db, e)
            pct = f"{st['pct']:.0f}%" if st["pct"] is not None else "s/d"
            lineas.append(f"- {e.nombre}: {st['compromisos']} compromisos, cumplimiento {pct}")
    desde = hoy() - timedelta(days=42)
    hall = list(db.scalars(select(Hallazgo).join(SesionCliente).where(SesionCliente.area_id == area.id,
                                                                       SesionCliente.fecha >= desde)))
    objs = list(db.scalars(select(Objecion).join(SesionCliente).where(SesionCliente.area_id == area.id,
                                                                       SesionCliente.fecha >= desde)))
    if hall:
        temas: dict[tuple[str, str], int] = {}
        for h in hall:
            temas[(h.cuadrante, h.tema)] = temas.get((h.cuadrante, h.tema), 0) + 1
        lineas.append("\nVoz del cliente (6 semanas, cuadrante · tema · menciones):")
        for (cu, t), n in sorted(temas.items(), key=lambda x: -x[1])[:12]:
            lineas.append(f"- {cu} · {t} · {n}")
    if objs:
        tipos: dict[str, list[int]] = {}
        for o in objs:
            tipos.setdefault(o.tipo, [0, 0])
            tipos[o.tipo][0] += 1
            tipos[o.tipo][1] += 1 if o.avanzo else 0
        lineas.append("\nObjeciones de ventas (6 semanas, tipo · veces · avanzó):")
        for t, (n, a) in tipos.items():
            lineas.append(f"- {t} · {n} · {a}")
    # recomendaciones anteriores con lo que pasó después
    prev = list(db.scalars(select(RecomendacionIA).where(RecomendacionIA.area_id == area.id,
                                                         RecomendacionIA.semana < semana)
                           .order_by(RecomendacionIA.semana.desc(), RecomendacionIA.orden).limit(9)))
    if prev:
        lineas.append("\nTus recomendaciones anteriores (código · semana · texto · qué pasó después):")
        for r in prev:
            ligados = list(db.scalars(select(Compromiso).where(Compromiso.recomendacion_id == r.id)))
            estado = (f"{len(ligados)} compromiso(s) creados: " + ", ".join(c.estado for c in ligados)) if ligados \
                else f"sin compromisos ligados (estado {r.estado})"
            lineas.append(f"- R{r.id} · {r.semana} · {r.texto[:220]} · {estado}")
    return "\n".join(lineas), prev


def _normalizar(res: dict, prev_ids: set[int]) -> dict:
    v = str(res.get("veredicto") or "").lower()
    out = {
        "veredicto": v if v in ("ganando", "en_riesgo", "perdiendo") else "en_riesgo",
        "titular": str(res.get("titular") or "")[:300],
        "lectura": str(res.get("lectura") or "")[:2500],
        "evaluacion": [],
        "recomendaciones": [],
        "investigacion": None,
    }
    for e in (res.get("evaluacion") or [])[:9]:
        if not isinstance(e, dict):
            continue
        try:
            rid = int(str(e.get("codigo") or "").lstrip("Rr"))
        except ValueError:
            continue
        if rid in prev_ids:
            ej = str(e.get("ejecucion") or "").lower()
            out["evaluacion"].append({"id": rid, "ejecucion": ej if ej in ("si", "parcial", "no") else "no",
                                      "efecto": str(e.get("efecto") or "")[:400]})
    for r in (res.get("recomendaciones") or [])[:3]:
        if isinstance(r, dict) and r.get("texto"):
            imp = str(r.get("impacto") or "medio").lower()
            out["recomendaciones"].append({"texto": str(r["texto"])[:600], "tecnica": str(r.get("tecnica") or "")[:200],
                                           "impacto": imp if imp in ("alto", "medio", "bajo") else "medio"})
    inv = res.get("investigacion")
    if isinstance(inv, dict) and inv.get("requerida") and inv.get("pregunta"):
        out["investigacion"] = str(inv["pregunta"])[:400]
    return out


INSTRUCCION = """Haz el diagnóstico semanal de esta área con tu especialidad. Primero evalúa tus recomendaciones anteriores
(si se ejecutaron y qué efecto tuvieron en el indicador), luego diagnostica la semana con el histórico y recomienda.
Reglas:
- Explica el resultado por sus causas: relaciona el movimiento del indicador con los compromisos cumplidos o no, las
  remediaciones que no se ejecutaron, las estrategias y la voz del cliente.
- Si una recomendación anterior no se ejecutó y sigue siendo válida, retómala en vez de inventar otra.
- Máximo 3 recomendaciones, concretas, para esta semana, cada una con la técnica o método que aplica.
- Pide investigación profunda (búsqueda web) SOLO si una decisión importante depende de información externa
  que no está en <datos> (regla nueva, referente de mercado, práctica de la industria).

<datos>
{contexto}
</datos>

JSON: {{"veredicto":"ganando|en_riesgo|perdiendo","titular":"una frase con el dato clave","lectura":"4-6 frases",
"evaluacion":[{{"codigo":"R123","ejecucion":"si|parcial|no","efecto":"qué pasó con el indicador"}}],
"recomendaciones":[{{"texto":"acción concreta","tecnica":"método o técnica","impacto":"alto|medio|bajo"}}],
"investigacion":{{"requerida":false,"pregunta":""}}}}"""


def investigar(db: Session, area: Area, pregunta: str, p: PromptArea | None) -> dict:
    prompt = f"""Investiga en fuentes confiables y actuales para responder esta pregunta del área {area.nombre} de OneCore.
Prioriza fuentes oficiales (DOF, SAT, ANAM), estudios de la industria y referentes reconocidos. Cita solo lo que encontraste.

<datos>
Pregunta: {pregunta}
</datos>

Al terminar, responde SOLO con JSON: {{"resumen":"3-5 frases con la respuesta","hallazgos":["hasta 5 hallazgos con su dato"],"implicacion":"qué significa para la decisión"}}"""
    res, meta = ia.cliente().investigar(prompt, system=system_area(p))
    return {"pregunta": pregunta, "resumen": str(res.get("resumen") or "")[:1500],
            "hallazgos": [str(x)[:400] for x in (res.get("hallazgos") or [])][:5],
            "implicacion": str(res.get("implicacion") or "")[:600], "fuentes": meta.get("fuentes", []),
            "fecha": hoy().isoformat(), "modelo": meta.get("modelo")}


def generar(db: Session, area: Area, semana: str | None = None, usuario_id: int | None = None,
            pregunta_manual: str | None = None) -> DiagnosticoSemanal:
    semana = semana or semana_iso(hoy())
    p = prompt_vigente(db, area)
    contexto, prev = _contexto(db, area, semana)
    prev_ids = {r.id for r in prev}
    system = system_area(p)
    res, meta = ia.cliente().pedir_json(INSTRUCCION.format(contexto=contexto), max_tokens=2500, system=system)
    out = _normalizar(res, prev_ids)
    investigacion = None
    pregunta = pregunta_manual or out["investigacion"]
    if pregunta and investigaciones_usadas(db, semana) < parametros.get(db, "investigaciones_por_semana"):
        try:
            investigacion = investigar(db, area, pregunta, p)
            ctx2 = contexto + "\n\nInvestigación profunda:\n" + investigacion["resumen"] + "\n" + \
                "\n".join(f"- {h}" for h in investigacion["hallazgos"]) + f"\nImplicación: {investigacion['implicacion']}"
            res2, meta = ia.cliente().pedir_json(INSTRUCCION.format(contexto=ctx2), max_tokens=2500, system=system)
            out = _normalizar(res2, prev_ids)
        except (ia.IANoDisponible, ValueError) as e:
            investigacion = {"pregunta": pregunta, "error": str(e)[:300]}
    for e in out["evaluacion"]:
        r = db.get(RecomendacionIA, e["id"])
        if r:
            r.ejecucion, r.efecto = e["ejecucion"], e["efecto"]
    d = DiagnosticoSemanal(area_id=area.id, semana=semana, veredicto=out["veredicto"], resultado=out,
                           investigacion=investigacion, contexto=contexto[:60000], prompt_area_id=p.id if p else None,
                           modelo=meta.get("modelo", ""), prompt_version=PROMPT_VERSION, generado_por=usuario_id)
    db.add(d)
    db.flush()
    for i, r in enumerate(out["recomendaciones"], 1):
        db.add(RecomendacionIA(diagnostico_id=d.id, area_id=area.id, semana=semana, orden=i, texto=r["texto"],
                               tecnica=r["tecnica"], impacto=r["impacto"]))
    auditar(db, "diagnostico", "area", area.id, usuario_id, actor="agente" if usuario_id is None else "usuario",
            semana=semana, investigacion=bool(investigacion))
    db.commit()
    return d


def ultimo(db: Session, area_id: int, semana: str | None = None) -> DiagnosticoSemanal | None:
    q = select(DiagnosticoSemanal).where(DiagnosticoSemanal.area_id == area_id)
    if semana:
        q = q.where(DiagnosticoSemanal.semana == semana)
    return db.scalar(q.order_by(DiagnosticoSemanal.creado.desc()).limit(1))


def efectividad(db: Session, area_id: int, semanas_atras: int = 4) -> dict:
    desde = semana_iso(hoy() - timedelta(weeks=semanas_atras))
    recs = list(db.scalars(select(RecomendacionIA).where(RecomendacionIA.area_id == area_id,
                                                         RecomendacionIA.semana >= desde)))
    evaluadas = [r for r in recs if r.ejecucion]
    return {"total": len(recs), "ejecutadas": sum(1 for r in evaluadas if r.ejecucion in ("si", "parcial")),
            "convertidas": sum(1 for r in recs if r.estado == "convertida")}


def semanal_todas(db: Session) -> int:
    n = 0
    sem = semana_iso(hoy())
    for a in db.scalars(select(Area).where(Area.activo.is_(True))):
        if not indicadores.mcis_de(db, a.id):
            continue
        if ultimo(db, a.id, sem):
            continue
        try:
            generar(db, a, sem)
            n += 1
        except (ia.IANoDisponible, ValueError):
            db.rollback()
    return n

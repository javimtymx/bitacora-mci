"""Círculo de Innovación: la IA consolida ideas, voz del cliente y objeciones en iniciativas; el círculo vota
con puntos (dot voting) y decide. Cada decisión crea la estrategia ligada a su MCI."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Area,
    CirculoInnovacion,
    Estrategia,
    EstrategiaEvento,
    Hallazgo,
    Idea,
    InsightBenchmark,
    PendienteCliente,
    SesionCompetidor,
    Iniciativa,
    Mci,
    Objecion,
    SesionCliente,
    Usuario,
    Voto,
)
from . import acciones, catalogo, diagnostico, ia
from .util import auditar, hoy

VENTANA_DIAS = 120


def fuentes(db: Session) -> dict:
    desde = hoy() - timedelta(days=VENTANA_DIAS)
    usadas = set()
    for ini in db.scalars(select(Iniciativa).where(Iniciativa.decision.in_(("estrategia", "proyecto")))):
        usadas |= set(ini.fuentes.get("ideas", []))
    ideas = [i for i in db.scalars(select(Idea).where(Idea.estado.in_(("validada", "propuesta", "en_curso"))))
             if i.id not in usadas and i.fortaleza != "debil"]
    hall = list(db.scalars(select(Hallazgo).join(SesionCliente).where(SesionCliente.fecha >= desde,
                                                                       Hallazgo.sentimiento != "positivo")))
    objs = list(db.scalars(select(Objecion).join(SesionCliente).where(SesionCliente.fecha >= desde)))
    # quejas y tareas de clientes (agenda mensual) que un líder confirmó; y conclusiones del benchmark de competidores
    quejas = list(db.scalars(select(PendienteCliente).where(PendienteCliente.estado.notin_(("propuesta", "descartado")),
                                                            PendienteCliente.creado >= desde)))
    bench = list(db.scalars(select(InsightBenchmark).join(SesionCompetidor).where(
        SesionCompetidor.fecha >= desde, InsightBenchmark.tipo.in_(("oportunidad", "suyo", "crear", "incrementar")))))
    return {"ideas": ideas, "hallazgos": hall, "objeciones": objs, "quejas": quejas, "benchmark": bench,
            "aprobadas": sum(1 for i in ideas if i.estado in ("validada", "en_curso"))}


def consolidar(db: Session, c: CirculoInnovacion, usuario: Usuario) -> CirculoInnovacion:
    f = fuentes(db)
    mcis = list(db.scalars(select(Mci).where(Mci.activo.is_(True))))
    li = "\n".join(f"- I{i.id} | {i.estado} | {i.titulo} | problema: {i.problema[:200]} | beneficio: {i.beneficio[:120]}"
                   for i in f["ideas"][:80]) or "- (ninguna)"
    lh = "\n".join(f"- H{h.id} | {h.cuadrante} | {h.tema} | «{h.cita[:160]}»" for h in f["hallazgos"][:150]) or "- (ninguno)"
    lo = "\n".join(f"- O{o.id} | {o.tipo} | «{o.cita[:160]}»" for o in f["objeciones"][:80]) or "- (ninguna)"
    lq = "\n".join(f"- Q{q.id} | {q.tipo} | {q.severidad} | {q.titulo} | «{(q.cita or q.detalle)[:160]}»"
                   for q in f["quejas"][:80]) or "- (ninguna)"
    lb = "\n".join(f"- B{b.id} | {b.tipo} | {b.sesion.competidor.nombre} | {b.titulo} | {b.detalle[:160]}"
                   for b in f["benchmark"][:80]) or "- (ninguno)"
    lm = "\n".join(f"- {m.clave} | {m.area.nombre} | {m.nombre}" for m in mcis)
    area = db.scalar(select(Area).where(Area.clave == "innovacion"))
    p = diagnostico.prompt_vigente(db, area) if area else None
    prompt = f"""Prepara el análisis para el Círculo de Innovación mensual de OneCore.
Consolida ideas del equipo (I), hallazgos de la voz del cliente (H), objeciones de ventas (O), quejas y tareas de
clientes de la agenda mensual (Q) y conclusiones del benchmark de competidores (B: oportunidades de servicio,
diferenciadores del competidor y lo que conviene crear o incrementar) en iniciativas: una
iniciativa junta todo lo que habla del MISMO problema. No inventes iniciativas sin fuentes. Cada iniciativa:
título corto, descripción (qué se haría), impacto 1-5 y esfuerzo 1-5 (justificados por la evidencia), la MCI que más
mueve (clave de la lista) y las fuentes que la sostienen. Elige como cita el código H u O más representativo.
Luego da una lectura estratégica: qué iniciativas atacan el mismo riesgo, cuáles compiten por la misma capacidad,
qué aprobarías ya y qué con condición. Lista las ideas que no consolidaste con el motivo.

<datos>
MCI (clave | área | nombre):
{lm}
Ideas del equipo:
{li}
Voz del cliente:
{lh}
Objeciones:
{lo}
Quejas y tareas de clientes:
{lq}
Benchmark de competidores:
{lb}
</datos>

JSON: {{"lectura":"5-7 frases","iniciativas":[{{"titulo":"...","descripcion":"...","impacto":4,"esfuerzo":2,"mci":"clave",
"fuentes":{{"ideas":["I1"],"hallazgos":["H3"],"objeciones":["O2"],"quejas":["Q4"],"benchmark":["B2"]}},"cita":"H3"}}],"sueltas":[{{"idea":"I7","motivo":"..."}}]}}
Máximo 8 iniciativas, ordenadas de mayor a menor evidencia."""
    res, meta = ia.cliente().pedir_json(prompt, max_tokens=4000, system=diagnostico.system_area(p))
    ids_i = {i.id for i in f["ideas"]}
    ids_h = {h.id: h for h in f["hallazgos"]}
    ids_o = {o.id: o for o in f["objeciones"]}
    ids_q = {q.id: q for q in f["quejas"]}
    ids_b = {b.id: b for b in f["benchmark"]}
    por_clave = {m.clave: m for m in mcis}

    def codigos(lista, pref, validos):
        out = []
        for x in lista or []:
            try:
                n = int(str(x).upper().lstrip(pref))
            except ValueError:
                continue
            if n in validos and n not in out:
                out.append(n)
        return out

    for ini in list(c.iniciativas):
        for v in list(ini.votos):
            db.delete(v)
        db.delete(ini)
    db.flush()
    orden = 0
    for it in (res.get("iniciativas") or [])[:8]:
        if not isinstance(it, dict) or not it.get("titulo"):
            continue
        fu = it.get("fuentes") if isinstance(it.get("fuentes"), dict) else {}
        fs = {"ideas": codigos(fu.get("ideas"), "I", ids_i), "hallazgos": codigos(fu.get("hallazgos"), "H", ids_h),
              "objeciones": codigos(fu.get("objeciones"), "O", ids_o), "quejas": codigos(fu.get("quejas"), "Q", ids_q),
              "benchmark": codigos(fu.get("benchmark"), "B", ids_b)}
        if not any(fs.values()):
            continue
        cita = ""
        ct = str(it.get("cita") or "").upper()
        if ct.startswith("H") and ct[1:].isdigit() and int(ct[1:]) in ids_h:
            cita = ids_h[int(ct[1:])].cita
        elif ct.startswith("O") and ct[1:].isdigit() and int(ct[1:]) in ids_o:
            cita = ids_o[int(ct[1:])].cita
        elif ct.startswith("Q") and ct[1:].isdigit() and int(ct[1:]) in ids_q:
            cita = ids_q[int(ct[1:])].cita or ids_q[int(ct[1:])].titulo
        elif ct.startswith("B") and ct[1:].isdigit() and int(ct[1:]) in ids_b:
            cita = ids_b[int(ct[1:])].cita or ids_b[int(ct[1:])].titulo
        elif fs["hallazgos"]:
            cita = ids_h[fs["hallazgos"][0]].cita
        imp = max(1, min(5, int(it.get("impacto") or 3)))
        esf = max(1, min(5, int(it.get("esfuerzo") or 3)))
        orden += 1
        m = por_clave.get(str(it.get("mci") or ""))
        db.add(Iniciativa(circulo_id=c.id, orden=orden, titulo=str(it["titulo"])[:200],
                          descripcion=str(it.get("descripcion") or "")[:1500], impacto=imp, esfuerzo=esf,
                          cuadrante=ia.cuadrante(imp, esf), mci_id=m.id if m else None, fuentes=fs, cita=cita[:1500]))
    sueltas = []
    for s in (res.get("sueltas") or [])[:30]:
        if isinstance(s, dict):
            n = codigos([s.get("idea")], "I", ids_i)
            if n:
                sueltas.append({"idea_id": n[0], "motivo": str(s.get("motivo") or "")[:300]})
    c.analisis = {"lectura": str(res.get("lectura") or "")[:3000], "sueltas": sueltas, "modelo": meta.get("modelo"),
                  "fuentes": {"ideas": len(f["ideas"]), "aprobadas": f["aprobadas"], "hallazgos": len(f["hallazgos"]),
                              "objeciones": len(f["objeciones"]), "quejas": len(f["quejas"]), "benchmark": len(f["benchmark"])}}
    c.estado = "votacion" if orden else "preparacion"
    auditar(db, "consolidar", "circulo", c.id, usuario.id, iniciativas=orden)
    db.commit()
    return c


def mis_puntos(db: Session, c: CirculoInnovacion, uid: int) -> dict[int, int]:
    ids = [i.id for i in c.iniciativas]
    return {v.iniciativa_id: v.puntos for v in db.scalars(select(Voto).where(Voto.usuario_id == uid,
                                                                             Voto.iniciativa_id.in_(ids)))} if ids else {}


def votar(db: Session, c: CirculoInnovacion, ini: Iniciativa, u: Usuario, puntos: int) -> str | None:
    if c.estado != "votacion":
        return "La votación no está abierta."
    puntos = max(0, min(c.max_por_iniciativa, puntos))
    mios = mis_puntos(db, c, u.id)
    usados = sum(v for k, v in mios.items() if k != ini.id)
    if usados + puntos > c.puntos_por_persona:
        return f"Solo te quedan {c.puntos_por_persona - usados} punto(s)."
    v = db.scalar(select(Voto).where(Voto.iniciativa_id == ini.id, Voto.usuario_id == u.id))
    if v:
        v.puntos = puntos
    else:
        db.add(Voto(iniciativa_id=ini.id, usuario_id=u.id, puntos=puntos))
    db.commit()
    return None


def conteo(c: CirculoInnovacion) -> list[tuple[Iniciativa, int]]:
    return sorted(((i, sum(v.puntos for v in i.votos)) for i in c.iniciativas), key=lambda x: (-x[1], x[0].orden))


def votantes(c: CirculoInnovacion) -> set[int]:
    return {v.usuario_id for i in c.iniciativas for v in i.votos if v.puntos > 0}


def decidir(db: Session, c: CirculoInnovacion, u: Usuario, decisiones: dict[int, dict]) -> int:
    creadas = 0
    innov = db.scalar(select(Area).where(Area.clave == "innovacion"))
    for ini in c.iniciativas:
        d = decisiones.get(ini.id)
        if not d:
            continue
        ini.decision = d["decision"]
        ini.responsable_id = d.get("responsable_id")
        ini.primera_revision = d.get("primera_revision")
        if ini.decision in ("estrategia", "proyecto") and not ini.estrategia_id:
            area_id = ini.mci.area_id if ini.mci else (innov.id if innov else None)
            if area_id is None:
                continue
            est = Estrategia(area_id=area_id, mci_id=ini.mci_id, nombre=(("Proyecto: " if ini.decision == "proyecto" else "")
                                                                          + ini.titulo)[:200],
                             descripcion=ini.descripcion, hipotesis=f"Decidida en {c.nombre} con evidencia de "
                             f"{len(ini.fuentes.get('ideas', []))} ideas, {len(ini.fuentes.get('hallazgos', []))} "
                             f"hallazgos de clientes, {len(ini.fuentes.get('objeciones', []))} objeciones, "
                             f"{len(ini.fuentes.get('quejas', []))} quejas o tareas de clientes y "
                             f"{len(ini.fuentes.get('benchmark', []))} conclusiones de benchmark.",
                             responsable_id=ini.responsable_id, seguimiento="semanal", creado_por=u.id)
            db.add(est)
            db.flush()
            db.add(EstrategiaEvento(estrategia_id=est.id, tipo="creada", estado_nuevo="propuesta",
                                    motivo=f"Iniciativa #{ini.orden} del {c.nombre}.", usuario_id=u.id))
            catalogo.cambiar_estado_estrategia(db, est, "activa", f"Aprobada por votación en el {c.nombre}.", u.id)
            ini.estrategia_id = est.id
            creadas += 1
            autores = set()
            for iid in ini.fuentes.get("ideas", []):
                idea = db.get(Idea, iid)
                if idea:
                    idea.estado = "en_curso"
                    autores.add(idea.autor_id)
            if autores:
                resp = db.get(Usuario, ini.responsable_id) if ini.responsable_id else None
                acciones.crear(db, tipo="correo", origen="circulo", titulo=f"Tu idea avanza: {ini.titulo}",
                               destinatarios=list(autores), asunto="Tu idea fue aprobada en el Círculo de Innovación",
                               cuerpo=(f"Tu idea forma parte de la iniciativa «{ini.titulo}», aprobada como {ini.decision} "
                                       f"en el {c.nombre}. La sigue {resp.nombre if resp else 'Dirección'}."),
                               entidad_tipo="estrategia", entidad_id=est.id, auto=True, creado_por=u.id)
    c.estado = "cerrado"
    auditar(db, "decidir", "circulo", c.id, u.id, estrategias=creadas)
    db.commit()
    return creadas

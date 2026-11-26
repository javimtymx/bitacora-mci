"""Propuesta de valor de una iniciativa del Círculo de Innovación.

Metodología (lienzo de propuesta de valor de Osterwalder + JTBD + historias de usuario):
1. evidencia  — sin IA. Junta las fuentes de la iniciativa y busca en TODO lo demás lo que habla del mismo problema:
                hallazgos y objeciones de todas las sesiones (retención, venta nueva, objeciones, agenda de clientes),
                ideas, pendientes de clientes, conclusiones del benchmark y fragmentos de las transcripciones de
                clientes y de competidores.
2. perfil     — trabajos del cliente (funcionales, sociales, emocionales), frustraciones y alegrías, con su evidencia
                y si hoy están resueltos.
3. mapa       — productos y servicios, aliviadores de frustraciones, creadores de alegrías, encaje y huecos,
                enunciado de la propuesta, diferenciador frente a competidores e hipótesis con experimento.
4. epicas     — funciones y épicas priorizadas (MoSCoW) ligadas a lo que atienden del perfil.
5. historias  — una llamada por épica: historias «Como… quiero… para…», criterios de aceptación y pantalla.
6. prototipo  — sin IA. Prompt detallado para pedirle a Claude un prototipo navegable de las pantallas.

Resiliencia a tiempos agotados:
- Cada etapa (y cada épica dentro de «historias») se guarda al terminar; al reanudar se sigue donde se quedó.
- Mientras corre, un latido se actualiza cada 20 s en otra conexión. Si el proceso muere (reinicio, tiempo agotado
  del servidor), el latido se detiene y a los LATIDO_VENCIDO segundos cualquiera puede reanudarlo: la pantalla lo
  hace sola al consultar el avance, y el job «ejecutar-acciones» (cada 15 min) reanuda los que quedaron atorados.
- Solo un proceso trabaja una propuesta a la vez (se reclama con un UPDATE condicionado).
- Fallas pasajeras de la IA: se reintenta la etapa hasta INTENTOS_ETAPA veces con espera creciente; si persiste,
  queda en error con el motivo y un botón para reintentar desde esa etapa.
- Cada llamada a la IA es acotada (evidencia recortada, máximo de elementos y de tokens) para no acercarse al límite.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from collections import Counter
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import flag_modified

from ..models import (
    Hallazgo,
    Idea,
    InsightBenchmark,
    Iniciativa,
    Objecion,
    PendienteCliente,
    PropuestaValor,
    SesionCliente,
    SesionCompetidor,
    Usuario,
    utcnow,
)
from . import ia
from .util import auditar

log = logging.getLogger("bitacora.propuesta_valor")

ETAPAS = [("evidencia", "Evidencia de todas las fuentes"), ("perfil", "Perfil del cliente"), ("mapa", "Mapa de valor"),
          ("epicas", "Funciones y épicas"), ("historias", "Historias de usuario"), ("prototipo", "Prompt de prototipo")]
NOMBRE_ETAPA = dict(ETAPAS)
LATIDO_CADA = 20          # segundos
LATIDO_VENCIDO = 180      # sin latido por más de esto: el proceso murió y se puede reanudar
INTENTOS_ETAPA = 3
ESPERAS = (5, 20, 60)
VENTANA_DIAS = 365
MAX_EVIDENCIA_CHARS = 26000
MAX_SESIONES_TEXTO = 80
STOP = set("""a al algo ante antes aqui asi aun cada como con contra cual cuando de del desde donde dos el ella ellos
en entre era es esa ese eso esta este esto estos fue han hasta hay la las le les lo los mas me mi muy nada ni no nos o
otra otro para pero poco por porque que quien se sea ser si sin sobre son su sus tambien te tiene todo tu un una uno
unos y ya hacer puede pueden debe deben tener cliente clientes onecore sistema sistemas mejor nuevo nueva usar uso""".split())
TIPOS_PRODUCTO = ("funcion", "modulo", "servicio", "integracion", "reporte", "capacitacion", "proceso")
PRIORIDADES = ("must", "should", "could", "wont")
PRIORIDAD_TXT = {"must": "Debe", "should": "Debería", "could": "Podría", "wont": "No por ahora"}


class EtapaFallida(Exception):
    pass


# ---------------------------------------------------------------- ciclo de vida

def ultima(db: Session, ini: Iniciativa) -> PropuestaValor | None:
    return db.scalar(select(PropuestaValor).where(PropuestaValor.iniciativa_id == ini.id)
                     .order_by(PropuestaValor.version.desc()).limit(1))


def iniciar(db: Session, ini: Iniciativa, u: Usuario) -> PropuestaValor:
    previa = ultima(db, ini)
    if previa and previa.estado in ("pendiente", "corriendo"):
        return previa  # ya hay una en proceso (si quedó atorada, correr() la reanuda)
    pv = PropuestaValor(iniciativa_id=ini.id, version=(previa.version + 1) if previa else 1, estado="pendiente",
                        etapas={k: {"estado": "pendiente", "intentos": 0} for k, _ in ETAPAS}, evidencia=[], perfil={},
                        mapa={}, epicas={}, creado_por=u.id)
    db.add(pv)
    db.flush()
    auditar(db, "propuesta_valor", "iniciativa", ini.id, u.id, version=pv.version)
    return pv


def vencida(pv: PropuestaValor) -> bool:
    """Corriendo sin señal de vida: el proceso se cayó (tiempo agotado, reinicio) y se puede reanudar."""
    if pv.estado == "pendiente":
        return pv.latido is None or pv.latido < utcnow() - timedelta(seconds=LATIDO_VENCIDO)
    return pv.estado == "corriendo" and (pv.latido is None or pv.latido < utcnow() - timedelta(seconds=LATIDO_VENCIDO))


def reintentar(db: Session, pv: PropuestaValor) -> None:
    """Después de un error: vuelve a intentar desde la etapa que falló (lo ya hecho se conserva)."""
    et = dict(pv.etapas or {})
    for k, _ in ETAPAS:
        if (et.get(k) or {}).get("estado") == "error":
            et[k] = {**et[k], "estado": "pendiente", "intentos": 0}
    pv.etapas, pv.estado, pv.error, pv.latido = et, "pendiente", "", None


def _reclamar(db: Session, pid: int) -> bool:
    """Toma la propuesta para trabajarla, si nadie más la está trabajando (UPDATE condicionado, seguro entre procesos)."""
    ahora = utcnow()
    limite = ahora - timedelta(seconds=LATIDO_VENCIDO)
    r = db.execute(update(PropuestaValor).where(
        PropuestaValor.id == pid,
        ((PropuestaValor.estado == "pendiente") | (PropuestaValor.estado == "corriendo"))
        & ((PropuestaValor.latido.is_(None)) | (PropuestaValor.latido < limite)),
    ).values(estado="corriendo", latido=ahora, actualizado=ahora).execution_options(synchronize_session=False))
    db.commit()
    return r.rowcount == 1


class _Latido:
    """Actualiza la señal de vida en otra conexión mientras una llamada larga a la IA está en curso."""

    def __init__(self, pid: int):
        self.pid, self._alto = pid, threading.Event()
        self._hilo = threading.Thread(target=self._latir, daemon=True)

    def _latir(self):
        from ..db import SessionLocal
        while not self._alto.wait(LATIDO_CADA):
            try:
                with SessionLocal() as s:
                    s.execute(update(PropuestaValor).where(PropuestaValor.id == self.pid, PropuestaValor.estado == "corriendo")
                              .values(latido=utcnow()))
                    s.commit()
            except Exception:  # noqa: BLE001
                log.warning("No se pudo actualizar el latido de la propuesta %s", self.pid, exc_info=True)

    def __enter__(self):
        self._hilo.start()
        return self

    def __exit__(self, *a):
        self._alto.set()


def correr(pid: int, dormir=time.sleep) -> str:
    """Avanza la propuesta etapa por etapa hasta terminar o fallar. Seguro de llamar varias veces."""
    from ..db import SessionLocal
    with SessionLocal() as db:
        if not _reclamar(db, pid):
            return "ocupada"
        pv = db.get(PropuestaValor, pid)
        with _Latido(pid):
            for clave, _ in ETAPAS:
                st = (pv.etapas or {}).get(clave) or {}
                if st.get("estado") == "listo":
                    continue
                while True:
                    _marcar(pv, clave, estado="corriendo", inicio=utcnow().isoformat(timespec="seconds"))
                    pv.etapa, pv.latido = clave, utcnow()
                    db.commit()
                    try:
                        ETAPA_FN[clave](db, pv)
                        _marcar(pv, clave, estado="listo", fin=utcnow().isoformat(timespec="seconds"), error="")
                        pv.latido, pv.actualizado = utcnow(), utcnow()
                        db.commit()
                        break
                    except Exception as e:  # noqa: BLE001
                        db.rollback()
                        pv = db.get(PropuestaValor, pid)
                        intentos = int(((pv.etapas or {}).get(clave) or {}).get("intentos", 0)) + 1
                        msg = str(e)[:300] if isinstance(e, (ia.IANoDisponible, ValueError, EtapaFallida)) else \
                            "Ocurrió un error inesperado en esta etapa."
                        log.warning("Propuesta %s, etapa %s, intento %s: %s: %s", pid, clave, intentos, type(e).__name__, e)
                        if intentos >= INTENTOS_ETAPA or not _reintentable(e):
                            _marcar(pv, clave, estado="error", intentos=intentos, error=msg)
                            pv.estado, pv.error, pv.latido = "error", f"{NOMBRE_ETAPA[clave]}: {msg}"[:500], None
                            db.commit()
                            return "error"
                        _marcar(pv, clave, estado="reintentando", intentos=intentos, error=msg)
                        pv.latido = utcnow()
                        db.commit()
                        dormir(ESPERAS[min(intentos - 1, len(ESPERAS) - 1)])
            pv.estado, pv.etapa, pv.error, pv.latido, pv.actualizado = "listo", "", "", utcnow(), utcnow()
            db.commit()
            return "listo"


def _reintentable(e: Exception) -> bool:
    if isinstance(e, ia.IANoDisponible):
        t = str(e).lower()
        return not any(k in t for k in ("saldo", "llave", "no existe", "falta anthropic"))
    return True  # JSON incompleto, tiempo agotado, error de red o de la base: vale la pena otro intento


def _marcar(pv: PropuestaValor, clave: str, **datos) -> None:
    et = copy.deepcopy(pv.etapas or {})
    et[clave] = {**(et.get(clave) or {}), **datos}
    pv.etapas = et
    flag_modified(pv, "etapas")


def reanudar_atoradas(db: Session) -> int:
    """Para el job periódico: reanuda las propuestas que quedaron a medias (proceso caído)."""
    n = 0
    for pv in db.scalars(select(PropuestaValor).where(PropuestaValor.estado.in_(("pendiente", "corriendo")))):
        if vencida(pv):
            n += 1
            correr(pv.id)
    return n


def avance(pv: PropuestaValor) -> dict:
    et = pv.etapas or {}
    hechas = sum(1 for k, _ in ETAPAS if (et.get(k) or {}).get("estado") == "listo")
    detalle = ""
    if pv.etapa == "historias" and pv.epicas.get("epicas"):
        eps = pv.epicas["epicas"]
        detalle = f"{sum(1 for e in eps if e.get('historias') is not None)} de {len(eps)} épicas"
    return {"estado": pv.estado, "etapa": pv.etapa, "nombre": NOMBRE_ETAPA.get(pv.etapa, ""), "hechas": hechas,
            "total": len(ETAPAS), "error": pv.error, "detalle": detalle, "vencida": vencida(pv),
            "etapas": [{"clave": k, "nombre": n, **(et.get(k) or {})} for k, n in ETAPAS]}


# ---------------------------------------------------------------- 1. evidencia (sin IA)

def _norm(t: str) -> str:
    from .vozcliente import _norm as n
    return n(t or "")


def _claves(textos: list[str], n: int = 30) -> list[str]:
    c = Counter()
    for t in textos:
        for w in _norm(t).split():
            if len(w) >= 4 and w not in STOP and not w.isdigit():
                c[w[:7]] += 1  # raíz simple: «agencia» y «agencias», «faltante» y «faltantes» se parecen por el inicio
    return [w for w, _ in c.most_common(n)]


def _puntaje(texto: str, claves: list[str]) -> float:
    t = _norm(texto)
    if not t:
        return 0.0
    pal = t.split()
    hits = sum(1 for w in pal if w[:7] in claves)
    distintas = len({w[:7] for w in pal if w[:7] in claves})
    return distintas * 2 + hits / max(1, len(pal)) * 10


def _fragmentos(texto: str, claves: list[str], n: int = 2, largo: int = 700) -> list[str]:
    pedazos, out = [], []
    for i in range(0, len(texto), largo // 2):
        p = texto[i:i + largo]
        if len(p) > 120:
            pedazos.append((_puntaje(p, claves), i, p))
    for sc, i, p in sorted(pedazos, reverse=True):
        if sc < 4 or any(abs(i - j) < largo for j, _ in out):
            continue
        out.append((i, p.strip()))
        if len(out) >= n:
            break
    return [p for _, p in out]


def etapa_evidencia(db: Session, pv: PropuestaValor) -> None:
    ini = pv.iniciativa
    fu = ini.fuentes or {}
    desde = (utcnow() - timedelta(days=VENTANA_DIAS)).date()
    items: list[dict] = []
    vistos: set[str] = set()

    def agregar(codigo, tipo, texto, cita="", origen="", url="", fecha="", ligada=False):
        if codigo in vistos or not (texto or cita):
            return
        vistos.add(codigo)
        items.append({"c": codigo, "tipo": tipo, "texto": (texto or "")[:500], "cita": (cita or "")[:400],
                      "origen": origen[:120], "url": url, "fecha": str(fecha or "")[:10], "ligada": ligada})

    def h_item(h: Hallazgo, ligada=False):
        s = h.sesion
        agregar(f"H{h.id}", "hallazgo", f"{h.cuadrante} · {h.tema}" + (f" · trabajo: {h.trabajo}" if h.trabajo else ""),
                h.cita, f"{_tipo_sesion(s)} · {s.cliente_nombre or s.cliente_rfc or 'cliente'}", f"/sesiones/{s.id}", s.fecha, ligada)

    def o_item(o: Objecion, ligada=False):
        s = o.sesion
        agregar(f"O{o.id}", "objecion", f"objeción {o.tipo}" + (f" · respuesta: {o.respuesta[:160]}" if o.respuesta else ""),
                o.cita, f"{_tipo_sesion(s)} · {s.cliente_nombre or 'prospecto'}", f"/sesiones/{s.id}", s.fecha, ligada)

    def b_item(b: InsightBenchmark, ligada=False):
        agregar(f"B{b.id}", "benchmark", f"{b.tipo} · {b.titulo} · {b.detalle[:250]}", b.cita,
                f"Competidor {b.sesion.competidor.nombre}", f"/clientes/benchmark/sesiones/{b.sesion_id}", b.sesion.fecha, ligada)

    def i_item(i: Idea, ligada=False):
        agregar(f"I{i.id}", "idea", f"{i.titulo} · problema: {i.problema[:250]} · beneficio: {(i.beneficio or '')[:150]}", "",
                f"Idea de {i.autor.nombre if i.autor else 'equipo'}", "/ideas", i.creado.date(), ligada)

    def q_item(q: PendienteCliente, ligada=False):
        agregar(f"Q{q.id}", "pendiente", f"{q.tipo} · {q.titulo} · {(q.detalle or '')[:200]}", q.cita or "",
                f"Pendiente de {q.cliente_nombre or 'cliente'}", f"/clientes/pendientes/{q.id}", q.creado.date(), ligada)

    # 1) lo que ya sostiene a la iniciativa
    for i in fu.get("ideas", []):
        x = db.get(Idea, i)
        if x:
            i_item(x, True)
    for i in fu.get("hallazgos", []):
        x = db.get(Hallazgo, i)
        if x:
            h_item(x, True)
    for i in fu.get("objeciones", []):
        x = db.get(Objecion, i)
        if x:
            o_item(x, True)
    for i in fu.get("quejas", []):
        x = db.get(PendienteCliente, i)
        if x:
            q_item(x, True)
    for i in fu.get("benchmark", []):
        x = db.get(InsightBenchmark, i)
        if x:
            b_item(x, True)
    base = [ini.titulo, ini.descripcion, ini.cita] + [it["texto"] + " " + it["cita"] for it in items]
    claves = _claves(base)
    pv.perfil = {**(pv.perfil or {}), "_claves": claves[:20]}

    # 2) lo relacionado en todas las categorías (no solo lo que la consolidación ligó)
    def mejores(objs, texto_fn, n):
        sc = [(_puntaje(texto_fn(o), claves), o) for o in objs]
        return [o for p, o in sorted(sc, key=lambda x: -x[0]) if p >= 4][:n]

    hall = db.scalars(select(Hallazgo).join(SesionCliente).where(SesionCliente.fecha >= desde))
    for h in mejores(hall, lambda h: f"{h.tema} {h.cita} {h.trabajo or ''} {h.oportunidad or ''}", 40):
        h_item(h)
    objs = db.scalars(select(Objecion).join(SesionCliente).where(SesionCliente.fecha >= desde))
    for o in mejores(objs, lambda o: f"{o.cita} {o.respuesta}", 15):
        o_item(o)
    bench = db.scalars(select(InsightBenchmark).join(SesionCompetidor).where(SesionCompetidor.fecha >= desde))
    for b in mejores(bench, lambda b: f"{b.titulo} {b.detalle} {b.cita}", 15):
        b_item(b)
    for i in mejores(db.scalars(select(Idea)), lambda i: f"{i.titulo} {i.problema} {i.beneficio}", 10):
        i_item(i)
    for q in mejores(db.scalars(select(PendienteCliente).where(PendienteCliente.estado != "no_vigente")),
                     lambda q: f"{q.titulo} {q.detalle} {q.cita}", 12):
        q_item(q)

    # 3) fragmentos de las transcripciones (clientes de todos los tipos y competidores), lo más relacionado
    from .vozcliente import leer_texto
    inicio = time.monotonic()
    frag = []
    ses = list(db.scalars(select(SesionCliente).where(SesionCliente.fecha >= desde).order_by(SesionCliente.fecha.desc())
                          .limit(MAX_SESIONES_TEXTO)))
    comp = list(db.scalars(select(SesionCompetidor).where(SesionCompetidor.fecha >= desde)
                           .order_by(SesionCompetidor.fecha.desc()).limit(MAX_SESIONES_TEXTO // 2)))
    for s in ses + comp:
        if time.monotonic() - inicio > 90:  # leer transcripciones de S3 no debe comerse la etapa
            log.warning("Evidencia: se cortó la lectura de transcripciones por tiempo")
            break
        try:
            texto = leer_texto(s)
        except Exception:  # noqa: BLE001  (cifrada sin llave, S3 caído): se sigue con las demás
            continue
        for p in _fragmentos(texto, claves):
            es_comp = isinstance(s, SesionCompetidor)
            frag.append((_puntaje(p, claves), p, s, es_comp))
    for k, (sc, p, s, es_comp) in enumerate(sorted(frag, key=lambda x: -x[0])[:24], 1):
        if es_comp:
            agregar(f"T{k}", "transcripcion_competidor", "", p, f"Transcripción · competidor {s.competidor.nombre}",
                    f"/clientes/benchmark/sesiones/{s.id}", s.fecha)
        else:
            agregar(f"T{k}", "transcripcion", "", p, f"Transcripción · {_tipo_sesion(s)} · {s.cliente_nombre or 'cliente'}",
                    f"/sesiones/{s.id}", s.fecha)
    if len(items) < 2:
        raise EtapaFallida("No hay evidencia suficiente para esta iniciativa (ni fuentes ligadas ni relacionadas).")
    pv.evidencia = items


def _tipo_sesion(s: SesionCliente) -> str:
    from .vozcliente import TIPOS_SESION
    return TIPOS_SESION.get(s.tipo, s.tipo)


def _bloque_evidencia(pv: PropuestaValor) -> str:
    lineas, total = [], 0
    orden = sorted(pv.evidencia or [], key=lambda x: (not x.get("ligada"), x["tipo"].startswith("transcripcion")))
    for it in orden:
        ln = (f"- {it['c']} | {it['tipo']} | {it['origen']} | {it['fecha']} | {it['texto'][:300]}"
              + (f" | «{it['cita'][:350]}»" if it.get("cita") else ""))
        if total + len(ln) > MAX_EVIDENCIA_CHARS:
            break
        lineas.append(ln)
        total += len(ln)
    return "\n".join(lineas)


def _codigos_validos(pv: PropuestaValor) -> set[str]:
    return {it["c"] for it in pv.evidencia or []}


def _ev(lista, validos: set[str]) -> list[str]:
    out = []
    for x in lista or []:
        c = str(x).strip().upper()
        if c in validos and c not in out:
            out.append(c)
    return out[:8]


def _contexto(pv: PropuestaValor) -> str:
    from .onecore import texto_portafolio
    ini = pv.iniciativa
    port = texto_portafolio(object_session(pv), 9000)
    return f"""Iniciativa del Círculo de Innovación de OneCore (software de cumplimiento de comercio exterior en México
para IMMEX, importadores, exportadores y agentes aduanales):
Título: {ini.titulo}
Descripción: {ini.descripcion}
Impacto {ini.impacto}/5 · esfuerzo {ini.esfuerzo}/5 · MCI: {ini.mci.nombre if ini.mci else '-'}
Lo que OneCore ya hace hoy (no propongas como nuevo lo que ya existe; extiéndelo):
{port}"""


# ---------------------------------------------------------------- 2. perfil del cliente

def etapa_perfil(db: Session, pv: PropuestaValor) -> None:
    prompt = f"""{_contexto(pv)}

Construye el PERFIL DEL CLIENTE del lienzo de propuesta de valor (Osterwalder) para esta iniciativa, solo con la
evidencia de abajo (voz del cliente, objeciones, ideas, pendientes, benchmark de competidores y fragmentos de
transcripciones). Cita los códigos de evidencia que sostienen cada elemento. Si algo no tiene evidencia, no lo
inventes; si lo infieres, márcalo "hipotesis": true.
- trabajos: lo que el cliente intenta lograr, en formato «Cuando…, quiero…, para…». tipo funcional|social|emocional.
  importancia 1-5.
- frustraciones: dolores, riesgos, obstáculos o malos resultados (multas, retrabajo, información no confiable,
  dependencia de terceros…). severidad 1-5; frecuencia alta|media|baja; "hoy": cómo lo resuelven hoy o qué atajo usan.
- alegrias: resultados o beneficios que espera o desea. tipo requerida|esperada|deseada|inesperada.
- Para cada elemento, "resuelto": no|parcial|si según la evidencia (¿OneCore u otro ya lo resuelve?).
- segmento: a quién describe (rol y tipo de empresa); competencia: cómo lo atienden los competidores según la evidencia.
Prioriza lo NO resuelto. Máximo 8 trabajos, 10 frustraciones y 8 alegrías.

<evidencia>
{_bloque_evidencia(pv)}
</evidencia>

JSON: {{"segmento":"...","competencia":"...","trabajos":[{{"texto":"Cuando…, quiero…, para…","tipo":"funcional",
"importancia":4,"resuelto":"no","evidencia":["H12"],"cita":"frase textual corta de la evidencia","hipotesis":false}}],
"frustraciones":[{{"texto":"...","severidad":4,"frecuencia":"alta","hoy":"...","resuelto":"parcial","evidencia":["O3"],
"cita":"...","hipotesis":false}}],"alegrias":[{{"texto":"...","tipo":"esperada","resuelto":"no","evidencia":["T2"],
"cita":"...","hipotesis":false}}]}}"""
    res, meta = ia.cliente().pedir_json(prompt, max_tokens=6000)
    v = _codigos_validos(pv)

    def lista(clave, pref, n, extra):
        out = []
        for k, x in enumerate((res.get(clave) or [])[:n], 1):
            if not isinstance(x, dict) or not str(x.get("texto") or "").strip():
                continue
            ev = _ev(x.get("evidencia"), v)
            item = {"id": f"{pref}{len(out) + 1}", "texto": str(x["texto"])[:400], "evidencia": ev,
                    "cita": str(x.get("cita") or "")[:300], "resuelto": _uno(x.get("resuelto"), ("no", "parcial", "si"), "no"),
                    "hipotesis": bool(x.get("hipotesis")) or not ev}
            item.update(extra(x))
            out.append(item)
        return out

    trabajos = lista("trabajos", "J", 8, lambda x: {"tipo": _uno(x.get("tipo"), ("funcional", "social", "emocional"), "funcional"),
                                                   "importancia": _num(x.get("importancia"), 3)})
    frus = lista("frustraciones", "F", 10, lambda x: {"severidad": _num(x.get("severidad"), 3),
                                                     "frecuencia": _uno(x.get("frecuencia"), ("alta", "media", "baja"), "media"),
                                                     "hoy": str(x.get("hoy") or "")[:300]})
    ale = lista("alegrias", "A", 8, lambda x: {"tipo": _uno(x.get("tipo"), ("requerida", "esperada", "deseada", "inesperada"), "esperada")})
    if not (trabajos or frus or ale):
        raise ValueError("La IA no devolvió el perfil del cliente. Se reintentará.")
    pv.perfil = {"segmento": str(res.get("segmento") or "")[:400], "competencia": str(res.get("competencia") or "")[:800],
                 "trabajos": trabajos, "frustraciones": frus, "alegrias": ale, "modelo": meta.get("modelo"),
                 "_claves": (pv.perfil or {}).get("_claves", [])}


def _uno(v, opciones, defecto):
    v = str(v or "").strip().lower().replace("í", "i")
    return v if v in opciones else defecto


def _num(v, defecto=3):
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return defecto


def _perfil_txt(pv: PropuestaValor) -> str:
    p = pv.perfil or {}
    out = [f"Segmento: {p.get('segmento', '')}", "Trabajos:"]
    out += [f"- {x['id']} ({x['tipo']}, importancia {x['importancia']}, resuelto {x['resuelto']}): {x['texto']}" for x in p.get("trabajos", [])]
    out.append("Frustraciones:")
    out += [f"- {x['id']} (severidad {x['severidad']}, {x['frecuencia']}, resuelto {x['resuelto']}): {x['texto']} · hoy: {x.get('hoy', '')}"
            for x in p.get("frustraciones", [])]
    out.append("Alegrías:")
    out += [f"- {x['id']} ({x['tipo']}, resuelto {x['resuelto']}): {x['texto']}" for x in p.get("alegrias", [])]
    out.append(f"Competencia: {p.get('competencia', '')}")
    return "\n".join(out)


# ---------------------------------------------------------------- 3. mapa de valor

def etapa_mapa(db: Session, pv: PropuestaValor) -> None:
    prompt = f"""{_contexto(pv)}

Perfil del cliente ya validado con evidencia:
{_perfil_txt(pv)}

Diseña el MAPA DE VALOR que encaja con ese perfil. Atiende primero lo NO resuelto y lo más severo o importante.
- productos: productos y servicios que OneCore puede crear o extender (función nueva, módulo, servicio, integración,
  reporte, capacitación, proceso). Di si extiende un módulo del portafolio o es nuevo.
- aliviadores: cómo cada producto elimina o reduce una frustración (ids F#); "como": el mecanismo concreto.
- creadores: cómo produce una alegría (ids A#) o facilita un trabajo (ids J#).
- propuesta: enunciado «Nuestro [producto] ayuda a [segmento] que quiere [trabajo] al [aliviar frustración] y
  [crear alegría], a diferencia de [alternativa/competidor]».
- diferenciador frente a competidores; hipotesis: supuestos críticos con experimento barato (Lean), métrica y
  criterio de éxito. Máximo 6 productos, 10 aliviadores, 8 creadores, 4 hipótesis.

JSON: {{"propuesta":"...","diferenciador":"...","productos":[{{"nombre":"...","tipo":"funcion","descripcion":"...",
"modulo":"SAFE o nuevo"}}],"aliviadores":[{{"texto":"...","alivia":["F1"],"producto":1,"como":"..."}}],
"creadores":[{{"texto":"...","crea":["A1","J2"],"producto":1}}],"hipotesis":[{{"supuesto":"...","experimento":"...",
"metrica":"...","criterio":"..."}}]}}"""
    res, meta = ia.cliente().pedir_json(prompt, max_tokens=6000)
    p = pv.perfil or {}
    ids_f = {x["id"] for x in p.get("frustraciones", [])}
    ids_a = {x["id"] for x in p.get("alegrias", [])} | {x["id"] for x in p.get("trabajos", [])}
    prods = []
    for x in (res.get("productos") or [])[:6]:
        if isinstance(x, dict) and x.get("nombre"):
            prods.append({"id": f"P{len(prods) + 1}", "nombre": str(x["nombre"])[:120], "descripcion": str(x.get("descripcion") or "")[:500],
                          "tipo": _uno(x.get("tipo"), TIPOS_PRODUCTO, "funcion"), "modulo": str(x.get("modulo") or "nuevo")[:60]})
    if not prods:
        raise ValueError("La IA no propuso productos ni servicios. Se reintentará.")

    def prod(v):
        try:
            k = int(str(v).upper().lstrip("P"))
        except ValueError:
            return ""
        return f"P{k}" if 1 <= k <= len(prods) else ""

    def refs(lista, validos):
        return [r for r in dict.fromkeys(str(y).strip().upper() for y in lista or []) if r in validos][:6]

    ali = [{"id": f"AL{k}", "texto": str(x.get("texto") or "")[:300], "alivia": refs(x.get("alivia"), ids_f),
            "producto": prod(x.get("producto")), "como": str(x.get("como") or "")[:300]}
           for k, x in enumerate([x for x in (res.get("aliviadores") or [])[:10] if isinstance(x, dict) and x.get("texto")], 1)]
    cre = [{"id": f"CR{k}", "texto": str(x.get("texto") or "")[:300], "crea": refs(x.get("crea"), ids_a), "producto": prod(x.get("producto"))}
           for k, x in enumerate([x for x in (res.get("creadores") or [])[:8] if isinstance(x, dict) and x.get("texto")], 1)]
    hip = [{"supuesto": str(x.get("supuesto") or "")[:300], "experimento": str(x.get("experimento") or "")[:300],
            "metrica": str(x.get("metrica") or "")[:200], "criterio": str(x.get("criterio") or "")[:200]}
           for x in (res.get("hipotesis") or [])[:4] if isinstance(x, dict) and x.get("supuesto")]
    pv.mapa = {"propuesta": str(res.get("propuesta") or "")[:800], "diferenciador": str(res.get("diferenciador") or "")[:600],
               "productos": prods, "aliviadores": ali, "creadores": cre, "hipotesis": hip, "encaje": encaje(p, ali, cre),
               "modelo": meta.get("modelo")}


def encaje(perfil: dict, ali: list[dict], cre: list[dict]) -> dict:
    """Lo calcula el sistema: qué del perfil queda atendido por el mapa y qué no (huecos), ponderado por severidad."""
    atendidas = {r for a in ali for r in a["alivia"]} | {r for c in cre for r in c["crea"]}
    peso_total = peso_cub = 0
    huecos = []
    for clave, peso in (("frustraciones", "severidad"), ("trabajos", "importancia"), ("alegrias", None)):
        for x in perfil.get(clave, []):
            w = x.get(peso, 3) if peso else 2
            w = w * (1.5 if x.get("resuelto") == "no" else 1)
            peso_total += w
            if x["id"] in atendidas:
                peso_cub += w
            elif x.get("resuelto") != "si":
                huecos.append(x["id"])
    return {"puntaje": round(peso_cub / peso_total * 100) if peso_total else 0, "huecos": huecos}


# ---------------------------------------------------------------- 4. funciones y épicas

def _mapa_txt(pv: PropuestaValor) -> str:
    m = pv.mapa or {}
    out = [f"Propuesta: {m.get('propuesta', '')}", "Productos y servicios:"]
    out += [f"- {x['id']} ({x['tipo']}, {x['modulo']}): {x['nombre']} — {x['descripcion']}" for x in m.get("productos", [])]
    out.append("Aliviadores:")
    out += [f"- {x['id']} → {', '.join(x['alivia'])} ({x['producto']}): {x['texto']}" for x in m.get("aliviadores", [])]
    out.append("Creadores de alegrías:")
    out += [f"- {x['id']} → {', '.join(x['crea'])} ({x['producto']}): {x['texto']}" for x in m.get("creadores", [])]
    return "\n".join(out)


def etapa_epicas(db: Session, pv: PropuestaValor) -> None:
    prompt = f"""{_contexto(pv)}

{_perfil_txt(pv)}

{_mapa_txt(pv)}

Descompón el mapa de valor en FUNCIONES (capacidades concretas del software o del servicio) y ÉPICAS entregables.
Cada épica: nombre, objetivo de negocio, qué funciones incluye, qué atiende del perfil (ids J#, F#, A#), prioridad
MoSCoW (must|should|could|wont) pensando en un primer entregable valioso, usuarios que la usan (rol) y la métrica
que dice que funcionó. Ordena de mayor a menor prioridad. Máximo 12 funciones y 7 épicas.

JSON: {{"funciones":[{{"nombre":"...","descripcion":"...","producto":"P1"}}],"epicas":[{{"nombre":"...","objetivo":"...",
"funciones":[1,2],"atiende":["F1","J2"],"prioridad":"must","usuarios":["Analista de comercio exterior"],"metrica":"..."}}]}}"""
    res, meta = ia.cliente().pedir_json(prompt, max_tokens=4500)
    p = pv.perfil or {}
    ids = {x["id"] for k in ("trabajos", "frustraciones", "alegrias") for x in p.get(k, [])}
    prods = {x["id"] for x in (pv.mapa or {}).get("productos", [])}
    funcs = []
    for x in (res.get("funciones") or [])[:12]:
        if isinstance(x, dict) and x.get("nombre"):
            pr = str(x.get("producto") or "").upper()
            funcs.append({"id": f"FN{len(funcs) + 1}", "nombre": str(x["nombre"])[:120], "descripcion": str(x.get("descripcion") or "")[:400],
                          "producto": pr if pr in prods else ""})
    eps = []
    for x in (res.get("epicas") or [])[:7]:
        if not isinstance(x, dict) or not x.get("nombre"):
            continue
        fn = []
        for f in x.get("funciones") or []:
            try:
                k = int(str(f).upper().lstrip("FN"))
            except ValueError:
                continue
            if 1 <= k <= len(funcs) and f"FN{k}" not in fn:
                fn.append(f"FN{k}")
        eps.append({"id": f"E{len(eps) + 1}", "nombre": str(x["nombre"])[:140], "objetivo": str(x.get("objetivo") or "")[:400],
                    "funciones": fn, "atiende": [r for r in dict.fromkeys(str(y).upper() for y in x.get("atiende") or []) if r in ids][:8],
                    "prioridad": _uno(x.get("prioridad"), PRIORIDADES, "should"),
                    "usuarios": [str(y)[:60] for y in (x.get("usuarios") or [])[:4]], "metrica": str(x.get("metrica") or "")[:200],
                    "historias": None})
    if not eps:
        raise ValueError("La IA no propuso épicas. Se reintentará.")
    orden = {k: i for i, k in enumerate(PRIORIDADES)}
    eps.sort(key=lambda e: orden[e["prioridad"]])
    for k, e in enumerate(eps, 1):
        e["id"] = f"E{k}"
    pv.epicas = {"funciones": funcs, "epicas": eps, "modelo": meta.get("modelo")}


# ---------------------------------------------------------------- 5. historias (una llamada por épica)

def etapa_historias(db: Session, pv: PropuestaValor) -> None:
    data = dict(pv.epicas or {})
    eps = [dict(e) for e in data.get("epicas", [])]
    funcs = {f["id"]: f for f in data.get("funciones", [])}
    for i, e in enumerate(eps):
        if e.get("historias") is not None:
            continue  # ya hecha en un intento anterior
        prompt = f"""{_contexto(pv)}

{_perfil_txt(pv)}

Épica {e['id']} ({PRIORIDAD_TXT[e['prioridad']]}): {e['nombre']}
Objetivo: {e['objetivo']}
Funciones: {'; '.join(funcs[f]['nombre'] + ' — ' + funcs[f]['descripcion'] for f in e['funciones'] if f in funcs) or '-'}
Atiende: {', '.join(e['atiende']) or '-'} · Usuarios: {', '.join(e['usuarios']) or '-'} · Métrica: {e['metrica']}

Escribe las HISTORIAS DE USUARIO de esta épica (INVEST: independientes, valiosas, estimables, pequeñas, probables).
Cada una: «Como [rol], quiero [acción], para [beneficio]», 2 a 4 criterios de aceptación en formato «Dado…, cuando…,
entonces…» con datos reales del dominio (pedimentos, RFC, fracciones, COVE, Anexo 24/30/31 según aplique), prioridad
MoSCoW, estimación S|M|L|XL, la pantalla donde ocurre (nombre corto y consistente entre historias) y los datos o
campos clave que se ven o capturan. Máximo 7 historias.

JSON: {{"historias":[{{"como":"...","quiero":"...","para":"...","criterios":["Dado…, cuando…, entonces…"],
"prioridad":"must","estimacion":"M","pantalla":"...","datos":["..."]}}]}}"""
        res, meta = ia.cliente().pedir_json(prompt, max_tokens=4500)
        hs = []
        for x in (res.get("historias") or [])[:7]:
            if not isinstance(x, dict) or not (x.get("quiero") or "").strip():
                continue
            hs.append({"id": f"{e['id']}-H{len(hs) + 1}", "como": str(x.get("como") or "")[:120], "quiero": str(x["quiero"])[:300],
                       "para": str(x.get("para") or "")[:300], "criterios": [str(c)[:300] for c in (x.get("criterios") or [])[:5]],
                       "prioridad": _uno(x.get("prioridad"), PRIORIDADES, e["prioridad"]),
                       "estimacion": _uno(x.get("estimacion"), ("s", "m", "l", "xl"), "m").upper(),
                       "pantalla": str(x.get("pantalla") or "General")[:80], "datos": [str(d)[:80] for d in (x.get("datos") or [])[:8]]})
        if not hs:
            raise ValueError(f"La IA no devolvió historias para la épica {e['id']}. Se reintentará.")
        eps[i] = {**e, "historias": hs}
        data["epicas"] = eps
        pv.epicas = copy.deepcopy(data)
        flag_modified(pv, "epicas")
        pv.latido = utcnow()
        db.commit()  # cada épica queda guardada: si la siguiente se cae, no se repite esta


# ---------------------------------------------------------------- 6. prompt de prototipo para Claude (sin IA)

def pantallas(pv: PropuestaValor) -> list[dict]:
    pant: dict[str, dict] = {}
    for e in (pv.epicas or {}).get("epicas", []):
        for h in e.get("historias") or []:
            p = pant.setdefault(h["pantalla"], {"nombre": h["pantalla"], "historias": [], "datos": [], "prioridad": "wont", "epicas": []})
            p["historias"].append(h)
            p["datos"] += [d for d in h["datos"] if d not in p["datos"]]
            if PRIORIDADES.index(h["prioridad"]) < PRIORIDADES.index(p["prioridad"]):
                p["prioridad"] = h["prioridad"]
            if e["id"] not in p["epicas"]:
                p["epicas"].append(e["id"])
    return sorted(pant.values(), key=lambda p: (PRIORIDADES.index(p["prioridad"]), p["nombre"]))


def etapa_prototipo(db: Session, pv: PropuestaValor) -> None:
    pv.prompt_prototipo = prompt_prototipo(pv)


def prompt_prototipo(pv: PropuestaValor) -> str:
    ini, p, m, e = pv.iniciativa, pv.perfil or {}, pv.mapa or {}, pv.epicas or {}
    pants = [x for x in pantallas(pv) if x["prioridad"] != "wont"]
    usuarios = sorted({u for ep in e.get("epicas", []) for u in ep.get("usuarios", [])})
    L = []
    L.append(f"# Prototipo navegable: {ini.titulo}")
    L.append("")
    L.append("Actúa como diseñador de producto senior y desarrollador front-end. Crea un PROTOTIPO NAVEGABLE de alta "
             "fidelidad como un artefacto (una sola página HTML con JavaScript, o un componente React) que pueda "
             "revisar con mi equipo y con clientes. No conectes APIs reales: usa datos de ejemplo realistas en el "
             "código. Todo el texto de la interfaz en español de México.")
    L.append("")
    L.append("## Contexto")
    L.append("OneCore es una empresa mexicana de software de cumplimiento de comercio exterior (IMMEX, importadores, "
             "exportadores y agentes aduanales). Módulos actuales: Smart Files (expediente documental), SAFE (validación "
             "de pedimentos antes del pago), Manifestación de Valor, CIVA, Activos Fijos, Reconstrucción y Análisis 360.")
    L.append(f"Iniciativa: {ini.titulo}. {ini.descripcion}")
    if p.get("segmento"):
        L.append(f"Para quién: {p['segmento']}")
    if usuarios:
        L.append(f"Usuarios del prototipo: {', '.join(usuarios)}.")
    L.append("")
    L.append("## Propuesta de valor")
    L.append(m.get("propuesta", ""))
    if m.get("diferenciador"):
        L.append(f"Diferenciador frente a la competencia: {m['diferenciador']}")
    L.append("")
    L.append("## Lo que el usuario intenta lograr (trabajos) y lo que hoy no está resuelto")
    for x in p.get("trabajos", []):
        if x.get("resuelto") != "si":
            L.append(f"- {x['texto']}")
    L.append("### Frustraciones que la interfaz debe eliminar o reducir")
    for x in sorted(p.get("frustraciones", []), key=lambda x: -x.get("severidad", 3)):
        if x.get("resuelto") != "si":
            L.append(f"- {x['texto']} (severidad {x['severidad']}/5; hoy: {x.get('hoy') or 'sin solución'})")
    L.append("### Alegrías que debe provocar")
    for x in p.get("alegrias", []):
        if x.get("resuelto") != "si":
            L.append(f"- {x['texto']} ({x['tipo']})")
    L.append("")
    L.append("## Cómo lo resolvemos (mapa de valor)")
    for x in m.get("productos", []):
        L.append(f"- **{x['nombre']}** ({x['tipo']}, {x['modulo']}): {x['descripcion']}")
    for x in m.get("aliviadores", []):
        L.append(f"  - Alivia: {x['texto']}" + (f" — {x['como']}" if x.get("como") else ""))
    for x in m.get("creadores", []):
        L.append(f"  - Crea valor: {x['texto']}")
    L.append("")
    L.append("## Pantallas a prototipar")
    L.append("Incluye una navegación clara entre ellas (barra lateral o pestañas) y una pantalla de inicio que lleve a cada una.")
    for k, pa in enumerate(pants, 1):
        L.append(f"### {k}. {pa['nombre']} ({PRIORIDAD_TXT[pa['prioridad']]})")
        for h in pa["historias"]:
            L.append(f"- Como {h['como']}, quiero {h['quiero']}, para {h['para']}.")
            for c in h["criterios"]:
                L.append(f"  - Criterio: {c}")
        if pa["datos"]:
            L.append(f"- Datos y campos que se ven o capturan: {', '.join(pa['datos'][:16])}.")
        L.append("- Estados: vacío (sin datos, con llamada a la acción), cargando, con datos, error con mensaje claro y "
                 "qué hacer, y confirmación de éxito.")
    L.append("")
    L.append("## Datos de ejemplo (realistas para México)")
    L.append("- Pedimentos con formato «26 43 3807 6001234» (año, aduana, patente, número), claves como A1, V1, IN, RT.")
    L.append("- RFC de empresas (p. ej. «MNO010203AB1»), fracciones arancelarias de 8 dígitos con NICO (p. ej. 8708.29.99 nico 99), "
             "fechas en formato dd/mm/aaaa, montos en MXN y USD con tipo de cambio del día.")
    L.append("- Nombres de empresas y agentes aduanales ficticios; de 8 a 20 registros por tabla con casos buenos y con problemas.")
    L.append("")
    L.append("## Lineamientos de diseño")
    L.append("- Interfaz limpia, profesional y densa en información (es software de trabajo diario), con jerarquía clara: "
             "primero lo que requiere acción, después el detalle.")
    L.append("- Semáforos y estatus con texto además de color; tablas con búsqueda, filtros y orden; acciones principales visibles.")
    L.append("- Responsivo (escritorio primero, usable en tableta), accesible (contraste AA, foco visible, etiquetas en formularios).")
    L.append("- Paleta sobria: fondo claro, azul petróleo oscuro como color principal, amarillo para la acción principal, "
             "rojo/ámbar/verde solo para estatus.")
    L.append("")
    L.append("## Entregable y criterios de revisión")
    L.append("1. Todas las pantallas anteriores navegables entre sí, con los datos de ejemplo.")
    L.append("2. Cada historia de usuario se puede recorrer en el prototipo; los criterios de aceptación se ven cumplidos "
             "(por ejemplo, al validar un dato inválido aparece el mensaje correspondiente).")
    L.append("3. Al final del artefacto, una sección «Notas del prototipo» con los supuestos que tomaste y preguntas abiertas.")
    if m.get("hipotesis"):
        L.append("4. El prototipo debe servir para validar estas hipótesis con clientes:")
        for h in m["hipotesis"]:
            L.append(f"   - {h['supuesto']} → experimento: {h['experimento']} (métrica: {h['metrica']}; éxito: {h['criterio']})")
    return "\n".join(L)


ETAPA_FN = {"evidencia": etapa_evidencia, "perfil": etapa_perfil, "mapa": etapa_mapa, "epicas": etapa_epicas,
            "historias": etapa_historias, "prototipo": etapa_prototipo}


# ---------------------------------------------------------------- exportar

def markdown(pv: PropuestaValor) -> str:
    ini, p, m, e = pv.iniciativa, pv.perfil or {}, pv.mapa or {}, pv.epicas or {}
    L = [f"# Propuesta de valor · {ini.titulo}", "", f"_Versión {pv.version} · {pv.actualizado:%d/%m/%Y}_", "",
         "## Perfil del cliente", f"**Segmento:** {p.get('segmento', '')}", ""]
    for titulo, clave in (("Trabajos", "trabajos"), ("Frustraciones", "frustraciones"), ("Alegrías", "alegrias")):
        L.append(f"### {titulo}")
        for x in p.get(clave, []):
            L.append(f"- **{x['id']}** {x['texto']} · resuelto: {x['resuelto']}" + (" · _hipótesis_" if x.get("hipotesis") else "")
                     + (f" · evidencia: {', '.join(x['evidencia'])}" if x.get("evidencia") else ""))
        L.append("")
    L += ["## Mapa de valor", f"**Propuesta:** {m.get('propuesta', '')}", f"**Diferenciador:** {m.get('diferenciador', '')}",
          f"**Encaje:** {(m.get('encaje') or {}).get('puntaje', 0)}% · huecos: {', '.join((m.get('encaje') or {}).get('huecos', [])) or 'ninguno'}", "",
          "### Productos y servicios"]
    L += [f"- **{x['id']} {x['nombre']}** ({x['tipo']}, {x['modulo']}): {x['descripcion']}" for x in m.get("productos", [])]
    L += ["", "### Aliviadores de frustraciones"]
    L += [f"- {x['texto']} → {', '.join(x['alivia'])}" for x in m.get("aliviadores", [])]
    L += ["", "### Creadores de alegrías"]
    L += [f"- {x['texto']} → {', '.join(x['crea'])}" for x in m.get("creadores", [])]
    L += ["", "### Hipótesis a validar"]
    L += [f"- {h['supuesto']} · experimento: {h['experimento']} · métrica: {h['metrica']} · éxito: {h['criterio']}" for h in m.get("hipotesis", [])]
    L += ["", "## Funciones"]
    L += [f"- **{f['id']} {f['nombre']}**: {f['descripcion']}" for f in e.get("funciones", [])]
    L += ["", "## Épicas e historias de usuario"]
    for ep in e.get("epicas", []):
        L += ["", f"### {ep['id']} · {ep['nombre']} ({PRIORIDAD_TXT[ep['prioridad']]})", ep["objetivo"],
              f"Atiende: {', '.join(ep['atiende'])} · Métrica: {ep['metrica']}"]
        for h in ep.get("historias") or []:
            L.append(f"- **{h['id']}** Como {h['como']}, quiero {h['quiero']}, para {h['para']}. ({h['prioridad']}, {h['estimacion']}, pantalla: {h['pantalla']})")
            L += [f"  - {c}" for c in h["criterios"]]
    L += ["", "## Evidencia"]
    L += [f"- {it['c']} · {it['origen']} · {it['fecha']}: {it['texto'][:200]}" + (f" «{it['cita'][:200]}»" if it.get("cita") else "")
          for it in pv.evidencia or []]
    return "\n".join(L)


def correr_varias(ids: list[int]) -> None:
    """«Profundizar todas»: una tras otra para no saturar el servicio de IA."""
    for pid in ids:
        try:
            correr(pid)
        except Exception:  # noqa: BLE001
            log.exception("Propuesta %s falló", pid)


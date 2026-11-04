"""Voz del cliente: análisis de transcripciones de sesiones 1:1 de retención y de objeciones de ventas.

Técnicas programadas (parámetros de descubrimiento):
- Retención: análisis temático con código mixto (4 cuadrantes fijos + temas que se reutilizan), sentimiento por
  tema, Jobs-to-be-Done para necesidades de producto y señales de riesgo de baja.
- Objeciones: clasificación por tipo, evaluación del manejo con LAER (escuchar, reconocer, explorar, responder),
  cobertura SPIN del descubrimiento y proporción de habla vendedor/cliente.
- Ambas: solo citas textuales verificadas contra la transcripción y verificación de que la sesión corresponde al
  compromiso (evidencia de ejecución)."""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CUADRANTES, TIPOS_OBJECION, Hallazgo, Objecion, SesionCliente
from . import ia
from .util import auditar, hoy

PROMPT_VERSION = "2026.11-vdc-1"
MAX_CHARS = 120_000


# ---------------------------------------------------------------- cifrado

def _fernet():
    k = get_settings().transcripciones_clave
    if not k:
        return None
    from cryptography.fernet import Fernet
    return Fernet(k.encode())


def guardar_texto(s: SesionCliente, texto: str) -> None:
    f = _fernet()
    s.palabras = len(texto.split())
    if f:
        s.transcripcion, s.cifrada = f.encrypt(texto.encode()).decode(), True
    else:
        s.transcripcion, s.cifrada = texto, False


def leer_texto(s: SesionCliente) -> str:
    if not s.cifrada:
        return s.transcripcion
    f = _fernet()
    if not f:
        raise RuntimeError("La transcripción está cifrada y falta TRANSCRIPCIONES_CLAVE.")
    return f.decrypt(s.transcripcion.encode()).decode()


def decodificar(raw: bytes) -> str:
    """Archivos de Teams/Zoom/Word suelen venir en UTF-8, UTF-16 (con BOM) o Windows-1252."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


# ---------------------------------------------------------------- verificación de citas

def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[«»\"“”'‘’.,;:¡!¿?()\-–—]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def cita_valida(cita: str, texto_norm: str) -> bool:
    c = _norm(cita)
    if len(c) < 12:
        return False
    if c in texto_norm:
        return True
    # tolera recortes: basta que un tramo largo de la cita aparezca tal cual
    palabras = c.split()
    if len(palabras) >= 8:
        n = max(6, int(len(palabras) * 0.7))
        for i in range(0, len(palabras) - n + 1):
            if " ".join(palabras[i:i + n]) in texto_norm:
                return True
    return False


# ---------------------------------------------------------------- prompts

def _temas_existentes(db: Session, area_id: int) -> str:
    rows = db.execute(select(Hallazgo.cuadrante, Hallazgo.tema).join(SesionCliente)
                      .where(SesionCliente.area_id == area_id).distinct().limit(80)).all()
    return "\n".join(f"- {c} · {t}" for c, t in rows) or "- (aún no hay temas)"


def _prompt_retencion(s, texto, compromiso_txt, temas):
    return f"""Analiza la transcripción de una sesión 1:1 de retención con un cliente de OneCore (software de cumplimiento aduanero).
Parámetros de descubrimiento:
1. Análisis temático: clasifica cada hallazgo en uno de 4 cuadrantes: producto (necesidades no cubiertas, ideas de
   producto, integraciones), plataforma (fallas, lentitud, disponibilidad, usabilidad), servicio (soporte, tiempos de
   respuesta, capacitación, acompañamiento), valor (precio, retorno percibido, competencia, renovación).
   Reutiliza un tema de la lista existente si es el mismo asunto; si no, crea uno corto (máx. 8 palabras).
2. Sentimiento por tema (positivo|neutral|negativo) y severidad (alta|media|baja).
3. Jobs-to-be-Done en producto: qué trabajo intenta resolver el cliente.
4. Señales de riesgo de baja: justificar costo, mencionar otro proveedor, cambio de patrocinador, baja de uso.
5. Marca normativo=true si el hallazgo nace de una regla o autoridad (SAT, ANAM, VUCEM, MVE, RGCE, Anexo 24/31).
6. Cada hallazgo lleva una CITA TEXTUAL copiada exacta de la transcripción (sin parafrasear). Sin cita, no hay hallazgo.
7. Evidencia: di si la transcripción corresponde a la sesión comprometida.

<datos>
Compromiso que esta sesión debe cumplir: {compromiso_txt or "(sesión sin compromiso ligado)"}
Fecha declarada: {s.fecha} · Participantes: {s.participantes}
Temas existentes (cuadrante · tema):
{temas}
Transcripción:
{texto}
</datos>

JSON: {{"evidencia":{{"corresponde":true,"motivo":"una frase"}},"resumen":"3-4 frases","riesgo_baja":"alto|medio|bajo",
"sentimiento":"positivo|neutral|negativo","hallazgos":[{{"cuadrante":"producto|plataforma|servicio|valor","tema":"...",
"cita":"texto exacto","sentimiento":"...","severidad":"alta|media|baja","tipo":"queja|necesidad|elogio|riesgo","normativo":false,
"jtbd":"solo en producto: el trabajo que intenta resolver"}}],"acuerdos":[{{"quien":"nosotros|cliente","texto":"...","fecha":"AAAA-MM-DD o vacío"}}]}}
Máximo 12 hallazgos."""


def _prompt_objeciones(s, texto, compromiso_txt, temas):
    tipos = ", ".join(TIPOS_OBJECION)
    return f"""Analiza la transcripción de una sesión comercial de OneCore donde surgieron objeciones.
Parámetros de descubrimiento:
1. Objeciones: clasifica cada una por tipo ({tipos}); cita textual exacta de lo que dijo el prospecto.
2. Manejo con LAER: "explorada" si el vendedor escuchó, reconoció y exploró con preguntas antes de responder;
   "sin_explorar" si respondió directo; "no_manejada" si la dejó pasar. Resume la respuesta usada y si la conversación
   avanzó a un siguiente paso concreto (avanzo=true/false).
3. Descubrimiento SPIN: cuenta preguntas del vendedor de situación, problema, implicación y necesidad-beneficio.
4. Proporción aproximada de habla del vendedor (0-100) y número de preguntas abiertas.
5. Descubrimiento del dolor: dolor declarado, impacto cuantificado (o "[pendiente]"), comprador económico, siguiente paso.
6. Hallazgos de producto o plataforma que mencione el prospecto, en los mismos 4 cuadrantes (producto, plataforma,
   servicio, valor), con cita exacta. Reutiliza temas existentes si es el mismo asunto.
7. Una recomendación de coaching concreta basada en la propia sesión.
8. Evidencia: di si la transcripción corresponde a la sesión comprometida.

<datos>
Compromiso que esta sesión debe cumplir: {compromiso_txt or "(sesión sin compromiso ligado)"}
Fecha declarada: {s.fecha} · Participantes: {s.participantes}
Temas existentes (cuadrante · tema):
{temas}
Transcripción:
{texto}
</datos>

JSON: {{"evidencia":{{"corresponde":true,"motivo":"..."}},"resumen":"3 frases","riesgo_baja":"",
"objeciones":[{{"tipo":"precio","cita":"texto exacto","manejo":"explorada|sin_explorar|no_manejada","respuesta":"...","avanzo":false}}],
"spin":{{"situacion":0,"problema":0,"implicacion":0,"necesidad":0}},"habla_vendedor":0,"preguntas_abiertas":0,
"descubrimiento":{{"dolor":"...","impacto":"...","comprador":"...","siguiente_paso":"..."}},"coaching":"...",
"hallazgos":[{{"cuadrante":"producto","tema":"...","cita":"texto exacto","sentimiento":"negativo","severidad":"media","tipo":"necesidad","normativo":false}}],
"acuerdos":[{{"quien":"nosotros|cliente","texto":"...","fecha":""}}]}}"""


TIPOS_SESION = {"retencion": "1:1 de retención", "objeciones": "Sesión comercial con objeciones",
                "venta_nueva": "Venta nueva · encaje de propuesta de valor"}

# Portafolio contra el que se evalúa el encaje. Ajustable aquí mientras no viva en el Catálogo.
PORTAFOLIO = [
    ("Smart Files", "expediente documental de comercio exterior"),
    ("SAFE", "validación de pedimentos antes del pago"),
    ("Manifestación de Valor", "MVE y respaldo de valor en aduana"),
    ("CIVA", "certificación IVA/IEPS y su control"),
    ("Activos Fijos", "control de activo fijo IMMEX"),
    ("Reconstrucción", "reconstrucción de operaciones y descargos"),
    ("Análisis 360", "informe mensual de cumplimiento del cliente"),
]
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


def limpiar_participantes(lista) -> list[dict]:
    out, vistos = [], set()
    for p in lista or []:
        if not isinstance(p, dict):
            continue
        nombre = " ".join(str(p.get("nombre") or "").split())[:120]
        email = str(p.get("email") or "").strip().lower()[:190]
        if not nombre and not email:
            continue
        clave = (nombre.lower() or email)
        if clave in vistos:
            continue
        vistos.add(clave)
        lado = str(p.get("lado") or "cliente").lower()
        out.append({"nombre": nombre or email.split("@")[0], "email": email if EMAIL_RE.match(email) else "",
                    "lado": lado if lado in ("cliente", "onecore") else "cliente",
                    "rol": str(p.get("rol") or "")[:80], "origen": str(p.get("origen") or "captura")[:12]})
    return out[:30]


def fusionar_participantes(actuales, detectados) -> list[dict]:
    """Conserva lo capturado y agrega lo que la IA detectó en la transcripción (sin pisar correos ya escritos)."""
    base = limpiar_participantes(actuales)
    idx = {p["nombre"].lower(): p for p in base}
    for d in limpiar_participantes([{**x, "origen": "detectado"} for x in (detectados or []) if isinstance(x, dict)]):
        p = idx.get(d["nombre"].lower())
        if p:
            p["email"] = p["email"] or d["email"]
            p["rol"] = p["rol"] or d["rol"]
        else:
            base.append(d)
            idx[d["nombre"].lower()] = d
    return base[:30]


def _prompt_venta_nueva(s, texto, compromiso_txt, temas):
    port = "\n".join(f"- {n}: {d}" for n, d in PORTAFOLIO)
    return f"""Analiza la transcripción de una sesión de venta nueva de OneCore (software de cumplimiento aduanero y comercio exterior en México) con el lienzo de propuesta de valor.
Lo valioso es lo que dijo el cliente y lo que se le ofreció. Cada elemento lleva una CITA TEXTUAL copiada exacta de la transcripción; sin cita, no se incluye.
1. Qué valora el cliente (criterios de decisión, resultados que busca).
2. Productos de OneCore que le interesaron o que reaccionó bien, con nivel de interés (alto|medio|bajo).
3. Perfil del cliente: trabajos (lo que intenta lograr), frustraciones (dolores, riesgos, multas, retrabajo) y ganancias esperadas.
   Para cada uno evalúa el encaje con nuestro portafolio:
   - "cubierto": un módulo actual lo resuelve (di cuál);
   - "parcial": lo resolvemos en parte (di qué falta);
   - "sin_encaje": hoy no lo cubrimos; describe la oportunidad (producto o servicio que podríamos ofrecer).
4. Ofertas: lo que nuestro equipo ofreció o propuso en la sesión (módulo, precio, piloto, demo), con cita.
5. Participantes: nombres que aparezcan en la transcripción, su correo si se menciona, si son del cliente u OneCore y su rol.
6. Siguiente paso acordado y acuerdos con responsable.
7. Evidencia: di si la transcripción corresponde a la sesión comprometida.

<datos>
Portafolio actual de OneCore:
{port}
Compromiso que esta sesión debe cumplir: {compromiso_txt or "(sesión sin compromiso ligado)"}
Fecha declarada: {s.fecha} · Participantes capturados: {s.participantes or "(no capturados)"}
Temas existentes de clientes:
{temas}
Transcripción:
{texto}
</datos>

JSON: {{"evidencia":{{"corresponde":true,"motivo":"..."}},"resumen":"3-4 frases","interes_general":"alto|medio|bajo",
"valora":[{{"texto":"qué valora, en una frase","cita":"texto exacto"}}],
"productos_interes":[{{"modulo":"nombre del portafolio","interes":"alto|medio|bajo","cita":"texto exacto"}}],
"encajes":[{{"tipo":"frustracion|trabajo|ganancia","texto":"una frase","cita":"texto exacto","encaje":"cubierto|parcial|sin_encaje","modulo":"módulo que lo cubre o vacío","oportunidad":"si es parcial o sin_encaje: qué podríamos ofrecer"}}],
"ofertas":[{{"oferta":"qué se ofreció","quien":"nombre de quien lo ofreció o vacío","cita":"texto exacto"}}],
"participantes":[{{"nombre":"...","email":"","lado":"cliente|onecore","rol":""}}],
"siguiente_paso":"...","acuerdos":[{{"quien":"nosotros|cliente","texto":"...","fecha":""}}]}}
Máximo 8 elementos por lista y 12 encajes."""


def _int(x, lo=0, hi=1000):
    try:
        return max(lo, min(hi, int(x)))
    except (TypeError, ValueError):
        return lo


UMBRAL_DOS_ETAPAS = 2500   # palabras: arriba de esto se sintetiza primero con el modelo rápido
TRAMO = 2200               # palabras por tramo en la síntesis
TRASLAPE = 120             # palabras que se repiten entre tramos para no cortar ideas

OBJETIVO_SINTESIS = {
    "retencion": "quejas, necesidades, elogios, riesgo de baja, uso del producto, precio y competencia",
    "objeciones": "objeciones del prospecto y cómo respondió el vendedor, dolor, impacto, quién decide y siguiente paso",
    "venta_nueva": "lo que valora el cliente, frustraciones, necesidades, productos que le interesaron, lo que se le ofreció, "
                   "precio, competencia y siguiente paso",
}


def _tramos(texto: str) -> list[str]:
    palabras = texto.split()
    out, i = [], 0
    while i < len(palabras):
        out.append(" ".join(palabras[max(0, i - TRASLAPE):i + TRAMO]))
        i += TRAMO
    return out


def sintetizar(s: SesionCliente, texto: str) -> tuple[str, list, dict]:
    """Etapa 1 (modelo rápido): de cada tramo copia textualmente solo lo relevante. Lo que no aparezca tal cual
    en la transcripción se descarta. Regresa el extracto, los participantes detectados y datos del proceso."""
    tn = _norm(texto)
    frags, participantes, fallas = [], [], 0
    tramos = _tramos(texto)
    for n, tr in enumerate(tramos, 1):
        prompt = f"""Parte {n} de {len(tramos)} de la transcripción de una sesión con cliente de OneCore (software de cumplimiento aduanero).
Copia TEXTUALMENTE (sin parafrasear, sin corregir) los fragmentos relevantes sobre: {OBJETIVO_SINTESIS.get(s.tipo, OBJETIVO_SINTESIS["retencion"])}; también acuerdos y compromisos con fecha.
Cada fragmento: exacto, de 8 a 60 palabras, con el hablante si se identifica. Omite saludos y charla sin contenido.
Anota nombres de participantes y correos si se mencionan.

<datos>
{tr}
</datos>

JSON: {{"fragmentos":[{{"hablante":"cliente|onecore|nombre","texto":"fragmento exacto"}}],"participantes":[{{"nombre":"...","email":"","lado":"cliente|onecore","rol":""}}]}} con máximo 25 fragmentos."""
        try:
            res, _meta = ia.cliente().pedir_json(prompt, rapido=True, max_tokens=4000)
        except (ia.IANoDisponible, ValueError) as e:
            fallas += 1
            ultimo_error = e
            continue
        for f in (res.get("fragmentos") or [])[:30]:
            t = str((f or {}).get("texto") or "").strip().strip("«»\"") if isinstance(f, dict) else ""
            if t and cita_valida(t, tn) and t not in {x[1] for x in frags}:
                frags.append((str(f.get("hablante") or "")[:40], t))
        participantes += [p for p in (res.get("participantes") or []) if isinstance(p, dict)]
    if fallas == len(tramos):
        raise ultimo_error  # noqa: F821 - sin ningún tramo sintetizado no hay extracto
    extracto = "\n".join(f"- [{h or '¿?'}] {t}" for h, t in frags)
    return extracto, participantes, {"modo": "dos_etapas", "tramos": len(tramos), "tramos_fallidos": fallas,
                                     "fragmentos": len(frags)}


def analizar(db: Session, s: SesionCliente) -> SesionCliente:
    texto = leer_texto(s)
    if len(texto) > MAX_CHARS:
        texto = texto[:MAX_CHARS]
    proceso = {"modo": "directo"}
    detectados_sintesis = []
    para_analizar = texto
    if len(texto.split()) > UMBRAL_DOS_ETAPAS:
        try:
            extracto, detectados_sintesis, proceso = sintetizar(s, texto)
            if proceso["fragmentos"] >= 5:
                para_analizar = ("[Extracto: fragmentos textuales de la transcripción en orden, seleccionados por relevancia]\n"
                                 + extracto)
            else:
                proceso = {"modo": "directo", "nota": "la síntesis encontró pocos fragmentos; se analizó completa"}
        except (ia.IANoDisponible, ValueError):
            proceso = {"modo": "directo", "nota": "la síntesis no respondió; se analizó la transcripción completa"}
    s_cfg = ia.cliente().s if hasattr(ia.cliente(), "s") else None
    proceso["modelo_sintesis"] = getattr(s_cfg, "ia_modelo_rapido", "") if proceso["modo"] == "dos_etapas" else ""
    proceso["modelo_analisis"] = getattr(s_cfg, "ia_modelo", "")
    comp_txt = s.compromiso.texto if s.compromiso else ""
    temas = _temas_existentes(db, s.area_id)
    fn = {"objeciones": _prompt_objeciones, "venta_nueva": _prompt_venta_nueva}.get(s.tipo, _prompt_retencion)
    prompt = fn(s, para_analizar, comp_txt, temas) + ("\nCitas: fragmentos exactos y breves (máximo 40 palabras cada una); "
                                              "frases cortas en todo lo demás.")
    res, meta = ia.cliente().pedir_json(prompt, max_tokens=8000)
    tn = _norm(texto)
    descartadas = 0
    for h in list(s.hallazgos):
        db.delete(h)
    for o in list(s.objeciones):
        db.delete(o)
    db.flush()
    hallazgos_out = []
    for h in (res.get("hallazgos") or [])[:15]:
        if not isinstance(h, dict):
            continue
        cita = str(h.get("cita") or "").strip().strip("«»\"")
        cu = str(h.get("cuadrante") or "").lower()
        if cu not in CUADRANTES or not cita_valida(cita, tn):
            descartadas += 1
            continue
        sent = str(h.get("sentimiento") or "neutral").lower()
        sev = str(h.get("severidad") or "media").lower()
        tp = str(h.get("tipo") or "queja").lower()
        obj = Hallazgo(sesion_id=s.id, cuadrante=cu, tema=str(h.get("tema") or "Sin tema")[:200].strip(), cita=cita[:1500],
                       sentimiento=sent if sent in ("positivo", "neutral", "negativo") else "neutral",
                       severidad=sev if sev in ("alta", "media", "baja") else "media",
                       tipo=tp if tp in ("queja", "necesidad", "elogio", "riesgo") else "queja",
                       normativo=bool(h.get("normativo")))
        db.add(obj)
        hallazgos_out.append({"jtbd": str(h.get("jtbd") or "")[:300], "tema": obj.tema})
    for o in (res.get("objeciones") or [])[:12] if s.tipo == "objeciones" else []:
        if not isinstance(o, dict):
            continue
        cita = str(o.get("cita") or "").strip().strip("«»\"")
        if not cita_valida(cita, tn):
            descartadas += 1
            continue
        tp = str(o.get("tipo") or "otra").lower()
        man = str(o.get("manejo") or "").lower()
        db.add(Objecion(sesion_id=s.id, tipo=tp if tp in TIPOS_OBJECION else "otra", cita=cita[:1500],
                        manejo=man if man in ("explorada", "sin_explorar", "no_manejada") else "sin_explorar",
                        respuesta=str(o.get("respuesta") or "")[:1000], avanzo=bool(o.get("avanzo"))))
    venta = {}
    if s.tipo == "venta_nueva":
        venta, d, nuevos = _venta_nueva(res, tn)
        descartadas += d
        for h in nuevos:  # las frustraciones sin encaje alimentan Voz del Cliente, Ideas y el Círculo
            db.add(h)
            h.sesion_id = s.id
        db.flush()
        mapa = {h.cita: h.id for h in nuevos}
        for e in venta["encajes"]:
            e["hallazgo_id"] = mapa.get(e["cita"])
    s.participantes_det = fusionar_participantes(s.participantes_det,
                                                 list(res.get("participantes") or []) + detectados_sintesis)
    ev = res.get("evidencia") if isinstance(res.get("evidencia"), dict) else {}
    rb = str(res.get("riesgo_baja") or "").lower()
    spin = res.get("spin") if isinstance(res.get("spin"), dict) else {}
    s.resultado = {
        "resumen": str(res.get("resumen") or "")[:1500],
        "sentimiento": str(res.get("sentimiento") or "")[:20],
        "evidencia": {"corresponde": bool(ev.get("corresponde")), "motivo": str(ev.get("motivo") or "")[:400]},
        "acuerdos": [{"quien": "nosotros" if str(a.get("quien")).lower().startswith("nos") else "cliente",
                      "texto": str(a.get("texto") or "")[:400], "fecha": str(a.get("fecha") or "")[:10]}
                     for a in (res.get("acuerdos") or [])[:8] if isinstance(a, dict) and a.get("texto")],
        "spin": {k: _int(spin.get(k), 0, 99) for k in ("situacion", "problema", "implicacion", "necesidad")} if spin else {},
        "habla_vendedor": _int(res.get("habla_vendedor"), 0, 100) if s.tipo == "objeciones" else None,
        "preguntas_abiertas": _int(res.get("preguntas_abiertas"), 0, 999) if s.tipo == "objeciones" else None,
        "descubrimiento": {k: str((res.get("descubrimiento") or {}).get(k) or "")[:400]
                           for k in ("dolor", "impacto", "comprador", "siguiente_paso")} if s.tipo == "objeciones" else {},
        "coaching": str(res.get("coaching") or "")[:800],
        "jtbd": [h for h in hallazgos_out if h["jtbd"]],
        "venta": venta,
        "proceso": proceso,
        "modelo": meta.get("modelo"), "prompt_version": PROMPT_VERSION,
    }
    s.riesgo_baja = rb if rb in ("alto", "medio", "bajo") else ""
    s.citas_descartadas = descartadas
    s.evidencia_verificada = bool(ev.get("corresponde")) and s.consentimiento
    s.estado = "analizada"
    auditar(db, "analizar", "sesion_cliente", s.id, s.autor_id, citas_descartadas=descartadas)
    db.commit()
    return s


def _venta_nueva(res: dict, tn: str) -> tuple[dict, int, list]:
    """Normaliza el análisis de venta nueva; descarta lo que no tenga cita verificable."""
    desc = 0

    def ok(c):
        nonlocal desc
        c = str(c or "").strip().strip("«»\"")
        if c and cita_valida(c, tn):
            return c[:1200]
        desc += 1
        return None

    def nivel(x):
        x = str(x or "").lower()
        return x if x in ("alto", "medio", "bajo") else "medio"

    modulos = {n.lower(): n for n, _ in PORTAFOLIO}
    valora = [{"texto": str(v.get("texto") or "")[:300], "cita": c} for v in (res.get("valora") or [])[:8]
              if isinstance(v, dict) and (c := ok(v.get("cita")))]
    prods = [{"modulo": modulos.get(str(v.get("modulo") or "").lower(), str(v.get("modulo") or "")[:80]),
              "interes": nivel(v.get("interes")), "cita": c}
             for v in (res.get("productos_interes") or [])[:8] if isinstance(v, dict) and (c := ok(v.get("cita")))]
    encajes, nuevos = [], []
    for v in (res.get("encajes") or [])[:12]:
        if not isinstance(v, dict) or not (c := ok(v.get("cita"))):
            continue
        enc = str(v.get("encaje") or "").lower()
        enc = enc if enc in ("cubierto", "parcial", "sin_encaje") else "parcial"
        tipo = str(v.get("tipo") or "frustracion").lower()
        tipo = tipo if tipo in ("frustracion", "trabajo", "ganancia") else "frustracion"
        e = {"tipo": tipo, "texto": str(v.get("texto") or "")[:300], "cita": c, "encaje": enc,
             "modulo": str(v.get("modulo") or "")[:80], "oportunidad": str(v.get("oportunidad") or "")[:300]}
        encajes.append(e)
        if enc != "cubierto":
            nuevos.append(Hallazgo(cuadrante="producto", tema=(e["oportunidad"] or e["texto"])[:200] or "Oportunidad de producto",
                                   cita=c, sentimiento="negativo" if tipo == "frustracion" else "neutral",
                                   severidad="alta" if enc == "sin_encaje" else "media", tipo="necesidad", normativo=False))
    ofertas = [{"oferta": str(v.get("oferta") or "")[:300], "quien": str(v.get("quien") or "")[:120], "cita": c}
               for v in (res.get("ofertas") or [])[:8] if isinstance(v, dict) and (c := ok(v.get("cita")))]
    return ({"interes_general": nivel(res.get("interes_general")), "valora": valora, "productos_interes": prods,
             "encajes": encajes, "ofertas": ofertas, "siguiente_paso": str(res.get("siguiente_paso") or "")[:400]},
            desc, nuevos)


def analizar_en_fondo(sid: int) -> None:
    """Corre el análisis fuera de la petición del navegador: si la conexión se cae, el análisis sigue."""
    from ..db import SessionLocal
    with SessionLocal() as db:
        s = db.get(SesionCliente, sid)
        if not s:
            return
        try:
            analizar(db, s)
        except Exception as e:  # noqa: BLE001 - cualquier falla queda registrada en la sesión
            db.rollback()
            s = db.get(SesionCliente, sid)
            s.estado = "error"
            s.resultado = {**(s.resultado or {}), "error": str(e)[:300] if isinstance(e, (ia.IANoDisponible, ValueError))
                           else "Ocurrió un error inesperado al analizar. Intenta de nuevo."}
            auditar(db, "analizar_error", "sesion_cliente", s.id, s.autor_id, error=str(e)[:300])
            db.commit()


def marcar_en_analisis(db: Session, s: SesionCliente) -> None:
    s.estado = "analizando"
    s.resultado = {k: v for k, v in (s.resultado or {}).items() if k != "error"} | {"desde": utcnow_iso()}
    db.commit()


def utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def analisis_atorado(s: SesionCliente, minutos: int = 12) -> bool:
    from datetime import datetime, timezone
    if s.estado != "analizando":
        return False
    try:
        desde = datetime.fromisoformat((s.resultado or {}).get("desde", ""))
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - desde).total_seconds() > minutos * 60


# ---------------------------------------------------------------- agregados para el módulo

def sesiones_periodo(db: Session, semanas: int = 12, tipo: str | None = None) -> list[SesionCliente]:
    q = select(SesionCliente).where(SesionCliente.estado == "analizada",
                                    SesionCliente.fecha >= hoy() - timedelta(weeks=semanas))
    if tipo:
        q = q.where(SesionCliente.tipo == tipo)
    return list(db.scalars(q))


def resumen(db: Session, semanas: int = 12, tipo: str | None = None) -> dict:
    ses = sesiones_periodo(db, semanas, tipo)
    ids = [s.id for s in ses]
    hall = list(db.scalars(select(Hallazgo).where(Hallazgo.sesion_id.in_(ids)))) if ids else []
    objs = list(db.scalars(select(Objecion).where(Objecion.sesion_id.in_(ids)))) if ids else []
    por_ses = {s.id: s for s in ses}
    mitad = hoy() - timedelta(weeks=semanas / 2)
    total = len(hall) or 1
    cuads = {}
    for k, nombre in CUADRANTES.items():
        hs = [h for h in hall if h.cuadrante == k]
        rec = [h for h in hs if por_ses[h.sesion_id].fecha >= mitad]
        ant = [h for h in hs if por_ses[h.sesion_id].fecha < mitad]
        temas: dict[str, int] = {}
        for h in hs:
            temas[h.tema] = temas.get(h.tema, 0) + 1
        neg = [h for h in hs if h.sentimiento == "negativo"]
        cuads[k] = {"nombre": nombre, "n": len(hs), "pct": len(hs) / total * 100,
                    "tendencia": (len(rec) - len(ant)),
                    "temas": sorted(temas.items(), key=lambda x: -x[1])[:3],
                    "cita": (neg or hs or [None])[0]}
    prio: dict[tuple[str, str], dict] = {}
    for h in hall:
        s = por_ses[h.sesion_id]
        d = prio.setdefault((h.cuadrante, h.tema), {"cuadrante": h.cuadrante, "tema": h.tema, "menciones": 0,
                                                    "clientes": set(), "riesgo": set(), "neg": 0, "normativo": False})
        d["menciones"] += 1
        d["clientes"].add(s.cliente_rfc or f"s{s.id}")
        if s.riesgo_baja == "alto":
            d["riesgo"].add(s.cliente_rfc or f"s{s.id}")
        d["neg"] += 1 if h.sentimiento == "negativo" else 0
        d["normativo"] |= h.normativo
    lista = []
    for d in prio.values():
        d["n_clientes"], d["n_riesgo"] = len(d["clientes"]), len(d["riesgo"])
        d["score"] = d["menciones"] * max(d["n_clientes"], 1) * (1 + d["n_riesgo"])
        lista.append(d)
    lista.sort(key=lambda x: -x["score"])
    lib: dict[str, dict] = {}
    for o in objs:
        d = lib.setdefault(o.tipo, {"tipo": o.tipo, "n": 0, "avanzo": 0, "cita": o.cita, "respuestas": {}})
        d["n"] += 1
        if o.avanzo:
            d["avanzo"] += 1
            d["respuestas"][o.respuesta] = d["respuestas"].get(o.respuesta, 0) + 1
    for d in lib.values():
        d["mejor"] = max(d["respuestas"].items(), key=lambda x: x[1])[0] if d["respuestas"] else ""
        d["pct"] = d["avanzo"] / d["n"] * 100 if d["n"] else 0
    return {"sesiones": ses, "n_sesiones": len(ses), "clientes": len({s.cliente_rfc or s.id for s in ses}),
            "citas": len(hall) + len(objs), "riesgo_alto": len({s.cliente_rfc or s.id for s in ses if s.riesgo_baja == "alto"}),
            "cuadrantes": cuads, "prioridad": lista[:10], "biblioteca": sorted(lib.values(), key=lambda x: -x["n"])}

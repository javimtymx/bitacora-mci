"""Propuesta de valor por iniciativa (por etapas, resiliente a tiempos agotados) y base «lo que hace OneCore» del benchmark."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (CirculoInnovacion, Hallazgo, Idea, Iniciativa, InsightBenchmark, ModuloOneCore, PropuestaValor,
                        SesionCliente, SesionCompetidor, Usuario, utcnow)
from app.services import ia
from app.services import propuesta_valor as pvs
from app.services.util import hoy
from conftest import login

TX = ("Cliente: cada mes el reporte de faltantes de expediente no cuadra con lo que la agencia dice que ya mandó. "
      "Perdemos dos días conciliando faltantes con la agencia aduanal y nadie sabe quién debe entregar cada documento. "
      "Nos gustaría que el sistema nos avise solo qué documento falta y a quién pedírselo. ") * 6


def _u(db, email="jmartinez@onecore.mx"):
    return db.scalar(select(Usuario).where(Usuario.email == email))


def _iniciativa(db):
    jm = _u(db)
    s = SesionCliente(tipo="retencion", area_id=jm.area_id, cliente_nombre="Toter de México", fecha=hoy() - timedelta(days=5),
                      transcripcion=TX, cifrada=False, palabras=300, estado="analizada", autor_id=jm.id, resultado={})
    otra = SesionCliente(tipo="venta_nueva", area_id=jm.area_id, cliente_nombre="Supply Solutions", fecha=hoy() - timedelta(days=9),
                         transcripcion="Prospecto: hablamos de precios y de capacitación general. " * 10, cifrada=False,
                         palabras=80, estado="analizada", autor_id=jm.id, resultado={})
    db.add_all([s, otra])
    db.flush()
    h1 = Hallazgo(sesion_id=s.id, cuadrante="frustracion", tema="Faltantes de expediente no confiables",
                  cita="el reporte de faltantes de expediente no cuadra", sentimiento="negativo", tipo="queja")
    h2 = Hallazgo(sesion_id=s.id, cuadrante="frustracion", tema="Conciliar faltantes con la agencia aduanal",
                  cita="Perdemos dos días conciliando faltantes con la agencia aduanal", sentimiento="negativo", tipo="queja")
    db.add_all([h1, h2])
    c = CirculoInnovacion(mes=hoy().strftime("%Y-%m"), nombre="Círculo prueba", estado="votacion")
    db.add(c)
    db.flush()
    ini = Iniciativa(circulo_id=c.id, orden=1, titulo="Faltantes confiables y agencias corresponsables",
                     descripcion="Reporte de faltantes de expediente confiable con responsable por documento",
                     impacto=5, esfuerzo=4, cuadrante="Proyecto", fuentes={"ideas": [], "hallazgos": [h1.id], "objeciones": []},
                     cita=h1.cita)
    db.add(ini)
    db.commit()
    return c, ini, h1, h2


PERFIL = {"segmento": "Gerente de comercio exterior IMMEX", "competencia": "GlobalMVE no lo cubre",
          "trabajos": [{"texto": "Cuando cierro el mes, quiero saber qué documento falta, para no tener observaciones",
                        "tipo": "funcional", "importancia": 5, "resuelto": "parcial", "evidencia": ["H{h1}", "T1"]}],
          "frustraciones": [{"texto": "El reporte de faltantes no es confiable", "severidad": 5, "frecuencia": "alta",
                             "hoy": "Concilian a mano con la agencia", "resuelto": "no", "evidencia": ["H{h2}", "X99"]},
                            {"texto": "Nadie sabe quién entrega cada documento", "severidad": 4, "resuelto": "no", "evidencia": []}],
          "alegrias": [{"texto": "Que el sistema avise solo a quién pedirlo", "tipo": "deseada", "resuelto": "no", "evidencia": ["T1"]}]}
MAPA = {"propuesta": "Nuestro Smart Files ayuda al gerente IMMEX…", "diferenciador": "Responsable por documento",
        "productos": [{"nombre": "Tablero de faltantes con responsable", "tipo": "funcion", "descripcion": "…", "modulo": "Smart Files"}],
        "aliviadores": [{"texto": "Conciliación automática", "alivia": ["F1", "F9"], "producto": 1, "como": "Cruza con la agencia"}],
        "creadores": [{"texto": "Aviso por documento", "crea": ["A1", "J1"], "producto": "P1"}],
        "hipotesis": [{"supuesto": "Las agencias responderán", "experimento": "Piloto con 2 agencias", "metrica": "% respuesta",
                       "criterio": ">70%"}]}
EPICAS = {"funciones": [{"nombre": "Conciliación de faltantes", "descripcion": "…", "producto": "P1"},
                        {"nombre": "Avisos a agencias", "descripcion": "…", "producto": "P1"}],
          "epicas": [{"nombre": "Avisos a la agencia", "objetivo": "…", "funciones": [2], "atiende": ["F2", "A1"], "prioridad": "should",
                      "usuarios": ["Agente aduanal"], "metrica": "tiempo de respuesta"},
                     {"nombre": "Faltantes confiables", "objetivo": "…", "funciones": ["FN1"], "atiende": ["F1", "J1", "ZZ"],
                      "prioridad": "must", "usuarios": ["Analista"], "metrica": "faltantes falsos"}]}


def _hist(pantalla):
    return {"historias": [{"como": "analista", "quiero": f"ver {pantalla}", "para": "actuar", "criterios": ["Dado…, cuando…, entonces…"],
                           "prioridad": "must", "estimacion": "m", "pantalla": pantalla, "datos": ["Pedimento", "Documento"]}]}


def _respuestas(h1, h2):
    import json
    p = json.loads(json.dumps(PERFIL).replace("{h1}", str(h1.id)).replace("{h2}", str(h2.id)))
    return [p, MAPA, EPICAS, _hist("Tablero de faltantes"), _hist("Avisos a agencias")]


def test_propuesta_de_valor_completa_por_etapas(admin, db, fake, monkeypatch):
    c, ini, h1, h2 = _iniciativa(db)
    assert "Propuesta de valor" in admin.get(f"/circulo/{c.id}").text
    assert "Qué va a hacer" in admin.get(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta").text
    fake.respuestas = _respuestas(h1, h2)
    admin.post(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta")  # corre en segundo plano (en pruebas, al terminar la petición)
    pv = db.scalar(select(PropuestaValor))
    db.refresh(pv)
    assert pv.estado == "listo", pv.error
    codigos = {e["c"] for e in pv.evidencia}
    assert f"H{h1.id}" in codigos and f"H{h2.id}" in codigos  # ligada y encontrada en otras fuentes
    assert any(e["tipo"] == "transcripcion" and "faltantes" in e["cita"] for e in pv.evidencia)  # fragmento de transcripción
    assert "Supply Solutions" not in str(pv.evidencia)  # la sesión que no trata el tema no entra
    perfil_prompt = fake.prompts[0]
    assert "Perdemos dos días conciliando" in perfil_prompt and "Smart Files" in perfil_prompt
    f = pv.perfil["frustraciones"]
    assert f[0]["evidencia"] == [f"H{h2.id}"] and not f[0]["hipotesis"] and f[1]["hipotesis"]  # sin evidencia = hipótesis
    m = pv.mapa
    assert m["aliviadores"][0]["alivia"] == ["F1"] and m["creadores"][0]["crea"] == ["A1", "J1"]
    assert m["encaje"]["huecos"] == ["F2"] and 0 < m["encaje"]["puntaje"] < 100
    eps = pv.epicas["epicas"]
    assert [e["prioridad"] for e in eps] == ["must", "should"] and eps[0]["id"] == "E1" and eps[0]["atiende"] == ["F1", "J1"]
    assert all(e["historias"] for e in eps)
    assert "Prototipo navegable" in pv.prompt_prototipo and "Tablero de faltantes" in pv.prompt_prototipo
    assert "Que el sistema avise solo a quién pedirlo" in pv.prompt_prototipo
    html = admin.get(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta").text
    for x in ("Lienzo de propuesta de valor", "Aliviadores de frustraciones", "No resuelto", "Prompt para prototipar en Claude",
              "Faltantes confiables", "Hipótesis a validar"):
        assert x in html, x
    md = admin.get(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta/{pv.id}.md").text
    assert "## Épicas e historias de usuario" in md and "E1-H1" in md
    assert admin.get(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta/{pv.id}.md?prompt=1").text == pv.prompt_prototipo
    # regenerar crea otra versión; la anterior se conserva
    fake.respuestas = _respuestas(h1, h2)
    admin.post(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta")
    assert [x.version for x in db.scalars(select(PropuestaValor).order_by(PropuestaValor.version))] == [1, 2]


def test_tiempo_agotado_reintenta_y_reanuda_donde_se_quedo(admin, db, fake, monkeypatch):
    c, ini, h1, h2 = _iniciativa(db)
    monkeypatch.setattr(pvs, "ESPERAS", (0, 0, 0))
    respuestas = _respuestas(h1, h2)
    llamadas = {"n": 0}
    original = fake.pedir_json

    def falla_en_segunda_epica(prompt, **kw):
        llamadas["n"] += 1
        if "Épica E2" in str(prompt):
            raise ia.IANoDisponible("No hubo conexión estable con el servicio de IA. Se reintentó varias veces.")
        return original(prompt, **kw)
    monkeypatch.setattr(fake, "pedir_json", falla_en_segunda_epica)
    fake.respuestas = respuestas[:4]
    admin.post(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta")
    pv = db.scalar(select(PropuestaValor))
    db.refresh(pv)
    assert pv.estado == "error" and "Historias de usuario" in pv.error
    assert pv.etapas["historias"]["intentos"] == pvs.INTENTOS_ETAPA and pv.etapas["epicas"]["estado"] == "listo"
    assert pv.epicas["epicas"][0]["historias"] and pv.epicas["epicas"][1]["historias"] is None  # E1 quedó guardada
    assert "Reintentar desde esa etapa" in admin.get(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta").text
    # se arregla la conexión y se reintenta: solo se pide la épica que faltaba
    monkeypatch.setattr(fake, "pedir_json", original)
    fake.respuestas = [respuestas[4]]
    antes = len(fake.prompts)
    admin.post(f"/circulo/{c.id}/iniciativas/{ini.id}/propuesta/{pv.id}/reintentar")
    db.refresh(pv)
    assert pv.estado == "listo" and len(fake.prompts) == antes + 1 and "Épica E2" in fake.prompts[-1]


def test_proceso_caido_se_reanuda_solo(admin, db, fake):
    c, ini, h1, h2 = _iniciativa(db)
    pv = pvs.iniciar(db, ini, _u(db))
    pv.estado, pv.latido = "corriendo", utcnow()  # alguien la está trabajando: nadie más la toma
    db.commit()
    assert pvs.correr(pv.id) == "ocupada"
    pv.latido = utcnow() - timedelta(seconds=pvs.LATIDO_VENCIDO + 60)  # el proceso murió (reinicio, tiempo agotado)
    db.commit()
    fake.respuestas = _respuestas(h1, h2)
    r = admin.get(f"/circulo/propuestas/{pv.id}/avance").json()  # la pantalla la consulta y la reanuda
    assert r["etapas"][0]["clave"] == "evidencia"
    db.refresh(pv)
    assert pv.estado == "listo"
    # el job periódico también las reanuda
    pv2 = pvs.iniciar(db, ini, _u(db))
    pv2.estado, pv2.latido = "corriendo", utcnow() - timedelta(hours=1)
    db.commit()
    fake.respuestas = _respuestas(h1, h2)
    from app import jobs
    assert "1 propuestas de valor reanudadas" in jobs.TAREAS["ejecutar-acciones"](db)
    db.refresh(pv2)
    assert pv2.estado == "listo"


def test_base_onecore_alimenta_benchmark_e_ideas(admin, db, fake):
    safe = db.scalar(select(ModuloOneCore).where(ModuloOneCore.nombre == "SAFE"))
    assert safe and "Base de comparación" in admin.get("/clientes/benchmark").text
    assert "Lo que hace OneCore" in admin.get(f"/clientes/benchmark/onecore?m={safe.id}").text
    # GuardIA ordena un texto pegado; no guarda hasta que la persona revisa
    fake.respuestas = [{"resumen": "Validación previa de pedimentos", "proceso": "1. Llega el pedimento\n2. Se valida",
                        "funciones": "Validación de fracción contra TIGIE", "no_hace": "No valida documentos por Incoterm"}]
    html = admin.post(f"/clientes/benchmark/onecore/{safe.id}/extraer", data={"texto": "Manual de SAFE. " * 20}).text
    assert "Propuesta de Guard" in html and "No valida documentos por Incoterm" in html
    db.refresh(safe)
    assert safe.no_hace == ""
    admin.post(f"/clientes/benchmark/onecore/{safe.id}", data={"resumen": "Validación previa de pedimentos",
                                                               "proceso": "1. Llega el pedimento del agente\n2. SAFE valida 120 reglas",
                                                               "funciones": "Validación de fracción contra TIGIE/NICO\nAlertas por correo",
                                                               "no_hace": "No valida documentos por Incoterm ni el formato E15"})
    db.refresh(safe)
    assert "Incoterm" in safe.no_hace and len(safe.historial) == 1
    # el análisis de un competidor compara contra ese detalle
    tx = ("Competidor: validamos documentos por Incoterm y damos el formato E15 listo para ventanilla. ") * 10
    fake.respuestas = [{"resumen": "r", "propuesta_competidor": "p",
                        "perfil": {"trabajos": [], "dolores": [{"texto": "Validar documentos por Incoterm", "onecore": "no"}], "ganancias": []},
                        "mapa": {"productos": [{"texto": "Formato E15", "onecore": "no"}], "aliviadores": [], "creadores": []},
                        "factores": [], "eric": {"crear": [{"titulo": "Validación por Incoterm", "detalle": "d",
                                                            "cita": "validamos documentos por Incoterm"}]},
                        "insights": [{"tipo": "suyo", "titulo": "E15 listo", "impacto": "alta",
                                      "cita": "damos el formato E15 listo para ventanilla"}],
                        "recomendacion": {"modificar": True, "que": "Agregar validación por Incoterm", "porque": "La piden"}}]
    r = admin.post("/clientes/benchmark/sesiones", data={"competidor_nuevo": "GlobalMve", "fecha": hoy().isoformat()},
                   files=[("archivo", ("t.txt", tx.encode(), "text/plain"))])
    assert r.status_code == 200, r.text[:300]
    assert "No valida documentos por Incoterm ni el formato E15" in fake.prompts[-1]
    s = db.scalar(select(SesionCompetidor))
    html = admin.get(f"/clientes/benchmark/sesiones/{s.id}").text
    assert html.count("A Ideas") >= 4 and "Base de comparación" in html
    # insight, elemento del lienzo y recomendación → Ideas a evaluar (una sola vez cada uno)
    ins = db.scalar(select(InsightBenchmark).where(InsightBenchmark.tipo == "suyo"))
    admin.post(f"/clientes/benchmark/insights/{ins.id}/idea")
    admin.post(f"/clientes/benchmark/insights/{ins.id}/idea")
    admin.post(f"/clientes/benchmark/sesiones/{s.id}/idea", data={"clave": "mapa.productos.0"})
    admin.post(f"/clientes/benchmark/sesiones/{s.id}/idea", data={"clave": "recomendacion"})
    admin.post(f"/clientes/benchmark/sesiones/{s.id}/idea", data={"clave": "recomendacion"})
    ideas = list(db.scalars(select(Idea).where(Idea.origen == "benchmark")))
    assert len(ideas) == 3 and {i.estado for i in ideas} == {"propuesta"}
    assert any("GlobalMve" in i.problema and "damos el formato E15" in i.problema for i in ideas)
    db.refresh(ins)
    assert ins.idea_id and "En Ideas" in admin.get(f"/clientes/benchmark/sesiones/{s.id}").text
    assert admin.post(f"/clientes/benchmark/sesiones/{s.id}/idea", data={"clave": "mapa.productos.9"}).status_code == 404


def test_onecore_si_lo_hace_alimenta_el_kardex(admin, db, fake):
    from app.services import innovacion
    tx = ("Competidor: tenemos reintento automático ante fallas de VUCEM y validamos documentos por Incoterm. ") * 8
    fake.respuestas = [{"resumen": "r", "propuesta_competidor": "p",
                        "perfil": {"trabajos": [], "dolores": [{"texto": "Transmitir aunque VUCEM falle", "onecore": "no"}], "ganancias": []},
                        "mapa": {"productos": [], "aliviadores": [], "creadores": []}, "factores": [], "eric": {},
                        "insights": [{"tipo": "suyo", "titulo": "Reintento automático ante fallas de VUCEM", "impacto": "media",
                                      "cita": "tenemos reintento automático ante fallas de VUCEM"},
                                     {"tipo": "oportunidad", "titulo": "Validación por Incoterm", "impacto": "alta",
                                      "cita": "validamos documentos por Incoterm"}],
                        "recomendacion": {"modificar": False, "que": "", "porque": ""}}]
    admin.post("/clientes/benchmark/sesiones", data={"competidor_nuevo": "Camtom", "fecha": hoy().isoformat()},
               files=[("archivo", ("t.txt", tx.encode(), "text/plain"))])
    s = db.scalar(select(SesionCompetidor))
    html = admin.get(f"/clientes/benchmark/sesiones/{s.id}").text
    assert "OneCore sí lo hace" in html and "¿Cómo lo hace OneCore?" in html
    ins = db.scalar(select(InsightBenchmark).where(InsightBenchmark.tipo == "suyo"))
    safe = db.scalar(select(ModuloOneCore).where(ModuloOneCore.nombre == "Smart Files"))
    # no basta con «sí lo hace»: se pide cómo
    admin.post(f"/clientes/benchmark/insights/{ins.id}/onecore", data={"modulo_id": safe.id, "nivel": "si", "como": "sí"})
    db.refresh(ins)
    assert ins.onecore == "" and not safe.confirmaciones
    # en parte, pide qué le falta
    admin.post(f"/clientes/benchmark/insights/{ins.id}/onecore", data={
        "modulo_id": safe.id, "nivel": "parcial", "como": "El robot de Smart Files reintenta la descarga de VUCEM cada hora durante 24 horas."})
    db.refresh(ins)
    assert ins.onecore == ""
    admin.post(f"/clientes/benchmark/insights/{ins.id}/onecore", data={
        "modulo_id": safe.id, "nivel": "parcial", "como": "El robot de Smart Files reintenta la descarga de VUCEM cada hora durante 24 horas.",
        "diferencia": "Además avisa por correo al analista", "falta": "No reintenta la transmisión del pedimento, solo la descarga"})
    db.refresh(ins)
    db.refresh(safe)
    assert ins.onecore == "parcial" and ins.onecore_modulo_id == safe.id
    k = safe.confirmaciones[0]
    assert k["competidor"] == "Camtom" and "reintenta la descarga" in k["como"] and "transmisión" in k["falta"]
    op = db.scalar(select(InsightBenchmark).where(InsightBenchmark.tipo == "oportunidad"))
    admin.post(f"/clientes/benchmark/insights/{op.id}/onecore", data={
        "modulo_id": safe.id, "nivel": "si", "volver": "/clientes/benchmark",
        "como": "Smart Files valida el tipo de documento según el Incoterm del pedimento y marca faltantes por operación."})
    db.refresh(op)
    assert op.onecore == "si"
    assert op not in innovacion.fuentes(db)["benchmark"] and ins in innovacion.fuentes(db)["benchmark"]  # «en parte» sigue siendo oportunidad
    panel = admin.get("/clientes/benchmark").text
    assert "OneCore lo hace en parte · Smart Files" in panel
    # lienzo
    admin.post(f"/clientes/benchmark/sesiones/{s.id}/onecore", data={"clave": "perfil.dolores.0", "modulo_id": safe.id, "nivel": "si",
                                                                      "como": "El robot sigue intentando la descarga de VUCEM y avisa cuando lo logra."})
    assert "OneCore sí lo hace · Smart Files" in admin.get(f"/clientes/benchmark/sesiones/{s.id}").text
    # la ficha del módulo muestra el kardex e integra con GuardIA
    db.refresh(safe)
    page = admin.get(f"/clientes/benchmark/onecore?m={safe.id}").text
    assert "Kardex" in page and "Camtom" in page and "Integrar a la ficha" in page
    fake.respuestas = [{"funciones": "Reintento de descarga de VUCEM\nValidación por Incoterm", "no_hace": "No reintenta la transmisión"}]
    page = admin.post(f"/clientes/benchmark/onecore/{safe.id}/integrar").text
    assert "Propuesta de Guard" in page and "Validación por Incoterm" in page and "Camtom" in fake.prompts[-1]
    # retirar con motivo: deja de usarse y el insight vuelve a quedar abierto
    admin.post(f"/clientes/benchmark/onecore/{safe.id}/kardex/{k['id']}/retirar", data={"motivo": "Lo confundimos con otra función"})
    db.refresh(ins)
    db.refresh(safe)
    assert ins.onecore == "" and safe.confirmaciones[0]["retirada"] and len(safe.confirmaciones) == 3
    # el siguiente análisis ya trae el cómo de lo confirmado (y no lo retirado); lo confirmado se conserva al re-analizar
    fake.respuestas = [{"resumen": "r", "insights": [{"tipo": "oportunidad", "titulo": "Validación por Incoterm", "impacto": "alta",
                                                     "cita": "validamos documentos por Incoterm"}]}]
    admin.post(f"/clientes/benchmark/sesiones/{s.id}/analizar")
    p = str(fake.prompts[-1])
    assert "valida el tipo de documento según el Incoterm" in p and "reintenta la descarga de VUCEM cada hora" not in p
    db.expire_all()
    nuevo = db.scalar(select(InsightBenchmark).where(InsightBenchmark.tipo == "oportunidad"))
    assert nuevo.onecore == "si"


def test_modal_de_factor_del_lienzo(admin, db, fake):
    from app.models import AnalisisFactor
    for nombre, val, nota in (("Camtom", 5, "Modelo de créditos sin contrato y reintento automático"), ("AVC", 3, "Paquete anual")):
        tx = (f"{nombre}: nuestro modelo comercial es flexible con créditos sin contrato y pago por uso para la MVE. ") * 8
        fake.respuestas = [{"resumen": "r", "factores": [{"factor": "Modelo comercial flexible", "competidor": val, "onecore": 2, "nota": nota}],
                            "insights": [{"tipo": "suyo", "titulo": "Modelo comercial por créditos", "impacto": "alta",
                                          "detalle": "Arranca con 50 créditos", "cita": "créditos sin contrato"}]}]
        admin.post("/clientes/benchmark/sesiones", data={"competidor_nuevo": nombre, "fecha": hoy().isoformat()},
                   files=[("archivo", ("t.txt", tx.encode(), "text/plain"))])
    panel = admin.get("/clientes/benchmark").text
    assert 'data-factor="modelo comercial flexible"' in panel and "Camtom · Modelo comercial flexible: 5/5" in panel
    frag = admin.get("/clientes/benchmark/factor", params={"k": "modelo comercial flexible"}).text
    assert "Camtom" in frag and "Modelo de créditos sin contrato" in frag and "Abajo del mejor por 3" in frag
    assert frag.index("Camtom") < frag.index("AVC") and "Modelo comercial por créditos" in frag and "Explicar con GuardIA" in frag
    assert admin.get("/clientes/benchmark/factor", params={"k": "no existe"}).status_code == 404
    fake.respuestas = [{"lectura": "Camtom gana por riesgo de entrada.", "que_hace_mejor": [
        {"competidor": "Camtom", "que": "Vende 50 créditos sin contrato", "por_que_gana": "El cliente prueba sin compromiso"}],
        "onecore_hoy": "Contrato anual", "acciones": [{"accion": "Piloto de pago por uso en MVE", "plazo": "corto", "modulo": "Manifestación de Valor",
                                                      "esfuerzo": "bajo", "impacto": "alto"}], "argumento": "Ofrecemos piloto."}]
    frag = admin.post("/clientes/benchmark/factor/explicar", data={"k": "modelo comercial flexible"}).text
    assert "Vende 50 créditos sin contrato" in frag and "Piloto de pago por uso en MVE" in frag and "≤ 30 días" in frag
    assert "Paquete anual" in fake.prompts[-1] and "créditos sin contrato" in fake.prompts[-1]
    a = db.scalar(select(AnalisisFactor))
    assert a and a.factor == "Modelo comercial flexible"
    # se guarda: al abrir de nuevo no se llama a la IA
    n = len(fake.prompts)
    assert "Vende 50 créditos" in admin.get("/clientes/benchmark/factor", params={"k": "modelo comercial flexible"}).text
    assert len(fake.prompts) == n


def test_onecore_tambien_lo_hace_desde_el_factor(admin, db, fake):
    for nombre, val in (("IMAR", 4), ("AVC", 3)):
        tx = (f"{nombre}: nosotros manejamos licencias por usuario con precio transparente y renovación anual. ") * 8
        fake.respuestas = [{"resumen": "r", "factores": [{"factor": "Transparencia de precio", "competidor": val, "onecore": 2,
                                                          "nota": f"{nombre} cotiza por usuario"}],
                            "eric": {"incrementar": [{"titulo": "Claridad de precio por usuario", "detalle": "Publicar esquema simple",
                                                      "cita": "manejamos licencias por usuario"}]}}]
        admin.post("/clientes/benchmark/sesiones", data={"competidor_nuevo": nombre, "fecha": hoy().isoformat()},
                   files=[("archivo", ("t.txt", tx.encode(), "text/plain"))])
    k = "transparencia de precio"
    frag = admin.get("/clientes/benchmark/factor", params={"k": k}).text
    assert "OneCore también lo hace" in frag and "OneCore sí lo hace" in frag  # corregir el factor y cada insight
    mvm = db.scalar(select(ModuloOneCore).where(ModuloOneCore.nombre == "Manifestación de Valor"))
    r = admin.post("/clientes/benchmark/factor/onecore", data={"k": k, "modulo_id": mvm.id, "calificacion": 4, "como": "corto"})
    assert r.status_code in (200, 303)
    db.refresh(mvm)
    assert not mvm.confirmaciones  # sin explicar cómo, no se acepta
    admin.post("/clientes/benchmark/factor/onecore", data={
        "k": k, "modulo_id": mvm.id, "calificacion": 4, "nivel": "si",
        "como": "Publicamos precio por módulo y por volumen de pedimentos en la propuesta, sin costos ocultos de implementación."})
    db.refresh(mvm)
    c = mvm.confirmaciones[0]
    assert c["factor"] == k and c["calificacion"] == 4 and c["calificacion_antes"] == 2 and c["competidor"] == "IMAR"
    from app.services import benchmark
    con = benchmark.consolidado(db)
    assert con["lienzo"]["onecore"][k] == 4.0  # el lienzo se recalcula con la corrección
    frag = admin.get("/clientes/benchmark/factor", params={"k": k}).text
    assert "Calificación corregida por el equipo" in frag and "los análisis decían 2" in frag and "Igual al mejor" in frag
    fake.respuestas = [{"lectura": "ok", "acciones": [{"accion": "x", "plazo": "corto"}]}]
    admin.post("/clientes/benchmark/factor/explicar", data={"k": k})
    assert "sin costos ocultos" in fake.prompts[-1] and "corregido por el equipo" in fake.prompts[-1]
    # si se retira del kardex, vuelve el promedio de los análisis
    admin.post(f"/clientes/benchmark/onecore/{mvm.id}/kardex/{c['id']}/retirar", data={"motivo": "Precio aún no publicado"})
    db.expire_all()
    assert benchmark.consolidado(db)["lienzo"]["onecore"][k] == 2.0

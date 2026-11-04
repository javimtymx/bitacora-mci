import io
import json

from sqlalchemy import select

from app.models import Ticket, TicketComentario, TicketPrompt, TicketRegla, Usuario
from app.services import almacen, ia
from app.services import tickets as tk


class FakeS3:
    def __init__(self):
        self.objetos = {}

    def put_object(self, Bucket, Key, Body, ContentType, ServerSideEncryption):
        assert ServerSideEncryption == "AES256"
        self.objetos[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objetos[Key])}

    def delete_object(self, Bucket, Key):
        self.objetos.pop(Key, None)


def _campos(**kw):
    c = {"descripcion": "Desde ayer al cargar el layout en SAFE sale valor en aduana inválido en todas las partidas",
         "producto": "SAFE", "menu": "Validación", "pantalla": "Pedimentos por validar", "tipo": "error",
         "impacto": "bloquea", "titulo": "", "pasos": "1. Validación 2. Cargar layout 3. Validar",
         "esperado": "Que valide", "obtenido": "valor en aduana inválido", "referencia": "", "cliente": ""}
    c.update(kw)
    return c


def test_revision_pide_lo_que_falta_y_chat_lo_llena(manuel, db, fake):
    fake.respuestas = [{"titulo": "SAFE: valor en aduana inválido al cargar layout",
                        "faltan": [{"campo": "referencia", "pregunta": "¿Con qué pedimento pasó?", "por_que": "Para reproducirlo"},
                                   {"campo": "inventado", "pregunta": "x"}],
                        "mensaje": "Me falta un dato"}]
    r = manuel.post("/api/tickets/revisar", json={"campos": _campos(), "anexos": []}).json()
    assert r["ok"] and not r["completo"] and [f["campo"] for f in r["faltan"]] == ["referencia"]
    assert "SAFE" in r["titulo"] and r["prompt_version"] == 1
    assert "Pedimentos por validar" in fake.prompts[-1] or "SAFE" in fake.prompts[-1]
    fake.respuestas = [{"campos": {"referencia": "26 19 3001 6001234"}, "faltan": [], "mensaje": "Listo, ya está todo."}]
    r = manuel.post("/api/tickets/revisar", json={"campos": _campos(), "anexos": [], "mensaje": "26 19 3001 6001234",
                                                  "historial": [{"de": "g", "t": "¿Con qué pedimento?"}]}).json()
    assert r["completo"] and r["campos"]["referencia"] == "26 19 3001 6001234"
    assert "<mensaje_nuevo>26 19 3001 6001234</mensaje_nuevo>" in fake.prompts[-1]


def test_sin_ia_pasa_a_manual(manuel, db, fake):
    fake.respuestas = [ia.IANoDisponible("timeout")]
    r = manuel.post("/api/tickets/revisar", json={"campos": _campos()}).json()
    assert r == {"ok": False, "sin_ia": True, "mensaje": "timeout"}
    # en manual el servidor exige los datos requeridos del tipo (salvo anexos)
    r = manuel.post("/api/tickets/nuevo", data={**_campos(), "validacion": "sin_ia"})
    assert r.status_code == 400 and "pedimento" in r.json()["mensaje"].lower()
    fake.respuestas = [ia.IANoDisponible("caído")]  # el triage en segundo plano tampoco responde
    r = manuel.post("/api/tickets/nuevo", data={**_campos(referencia="26 19 3001 6001234"), "validacion": "sin_ia"})
    assert r.status_code == 200 and r.json()["ok"]
    t = db.scalar(select(Ticket))
    db.refresh(t)
    assert t.validacion == "sin_ia" and t.titulo.startswith("SAFE · Pedimentos por validar")
    assert t.ia_triage["origen"] == "reglas" and t.ia_triage["prioridad"] == "P1"


def test_alta_con_anexo_en_s3_y_flujo_completo(manuel, admin, mario, db, fake):
    s3 = FakeS3()
    almacen.set_s3(s3)
    try:
        fake.respuestas = [{"prioridad": "P2", "motivo": "Afecta a un cliente", "responsable": "jmartinez@onecore.mx",
                            "resumen": "Error de validación", "titulo": ""}]
        r = manuel.post("/api/tickets/nuevo", data={**_campos(), "validacion": "incompleto",
                                                    "faltantes": json.dumps(["referencia"]),
                                                    "conversacion": json.dumps([{"de": "g", "t": "¿Pedimento?"}, {"de": "u", "t": "No lo tengo"}])},
                        files=[("archivos", ("layout.txt", b"501|1|valor", "text/plain"))])
        assert r.json()["ok"], r.text
        t = db.scalar(select(Ticket))
        db.refresh(t)
        assert t.folio == f"ONE-{t.id}" and t.faltantes == ["referencia"] and len(t.captura["conversacion"]) == 2
        a = t.anexos[0]
        assert a.ruta.startswith("s3:bitacora-mci/tickets/") and s3.objetos[a.ruta[3:]] == b"501|1|valor"
        jm = db.scalar(select(Usuario).where(Usuario.email == "jmartinez@onecore.mx"))
        assert t.ia_triage["prioridad"] == "P2" and t.ia_triage["responsable_id"] == jm.id
        # quien levantó sí ve y descarga; otro colaborador no
        assert manuel.get(f"/anexos-ticket/{a.id}").content == b"501|1|valor"
        assert mario.get(f"/tickets/{t.id}").status_code in (403, 404)
        # el colaborador no puede hacer triage ni mover
        assert manuel.post(f"/tickets/{t.id}/mover", data={"estado": "en_curso"}).status_code == 403
        # triage
        admin.post(f"/tickets-triage/{t.id}", data={"prioridad": "P2", "asignado_id": jm.id})
        db.refresh(t)
        assert t.estado == "pendiente" and t.sla_vence is not None and t.asignado_id == jm.id
        # desarrollo pide información y propone regla
        admin.post(f"/tickets/{t.id}/pedir-info", data={"pregunta": "Necesito el número de pedimento, por favor.",
                                                        "regla": "En SAFE pide siempre el pedimento y la aduana."})
        db.refresh(t)
        assert t.estado == "info"
        manuel.post(f"/tickets/{t.id}/comentar", data={"texto": "Es el 26 19 3001 6001234"})
        db.refresh(t)
        assert t.estado == "pendiente"
        assert [c.tipo for c in t.comentarios][-2:] == ["pedir_info", "respuesta"]
        # la regla se aprueba y crea una versión nueva del prompt
        regla = db.scalar(select(TicketRegla))
        admin.post(f"/tickets-config/reglas/{regla.id}", data={"accion": "aprobar", "texto": regla.texto})
        p = db.scalar(select(TicketPrompt).order_by(TicketPrompt.version.desc()))
        assert p.version == 2 and "pedimento y la aduana" in p.reglas[-1]
        # paquete para Cursor y cierre obligatorio con causa y solución
        fake.respuestas = ["# ONE\n\n## Qué lograr\nQue el layout valide el valor en aduana con la factura correcta."]
        admin.post(f"/tickets/{t.id}/paquete")
        db.refresh(t)
        assert "Qué lograr" in t.paquete_cursor
        admin.post(f"/tickets/{t.id}/cerrar", data={"tipo_cierre": "corregido", "causa": "x", "solucion": "y"})
        db.refresh(t)
        assert t.estado == "pendiente"  # sin causa y solución no se cierra
        admin.post(f"/tickets/{t.id}/cerrar", data={"tipo_cierre": "corregido",
                                                    "causa": "El tipo de cambio se tomaba del día de pago y no de la entrada",
                                                    "solucion": "Se corrigió la fecha del tipo de cambio en la validación del layout"})
        db.refresh(t)
        assert t.estado == "cerrado" and t.resolucion["por_nombre"]
        # el cierre alimenta los similares
        sim = tk.similares(db, "SAFE layout valor en aduana inválido partidas")
        assert sim and sim[0]["folio"] == t.folio
        html = admin.get("/tickets-config").text
        assert "Versión 2" in html and "50%" not in html  # 1 ticket de la versión 1, regresado → 100%
        assert "100%" in html
    finally:
        almacen.set_s3(None)


def test_pantallas_y_menus(admin, manuel, db):
    assert manuel.get("/tickets/nuevo").status_code == 200
    assert "Levantar ticket" in manuel.get("/tickets").text
    for url in ("/tickets-triage", "/tickets-tablero", "/tickets-config"):
        assert manuel.get(url).status_code == 403, url
        assert admin.get(url).status_code == 200, url
    assert "SAFE" in admin.get("/tickets-config").text  # catálogo inicial
    r = admin.post("/tickets-config/catalogo", data={"catalogo": "SAFE › Validación › Pedimentos por validar\nCIVA > Reportes"})
    assert "SAFE › Validación › Pedimentos por validar" in tk.catalogo_texto(db)
    assert tk.catalogo(db) == {"CIVA": {"Reportes": []}, "SAFE": {"Validación": ["Pedimentos por validar"]}}


def test_transcripcion_y_evidencia_en_s3(db):
    from app.models import SesionCliente
    from app.services import vozcliente
    s3 = FakeS3()
    almacen.set_s3(s3)
    try:
        s = SesionCliente(tipo="retencion", area_id=1, fecha=__import__("datetime").date.today(), autor_id=1, transcripcion="")
        vozcliente.guardar_texto(s, "hola cliente " * 50)
        assert s.transcripcion == "" and s.transcripcion_ruta.startswith("s3:bitacora-mci/transcripciones/")
        assert b"hola" not in s3.objetos[s.transcripcion_ruta[3:]]  # va cifrada
        assert vozcliente.leer_texto(s).startswith("hola cliente")
    finally:
        almacen.set_s3(None)


def test_menus_de_desarrollo_se_otorgan(admin, mario, db):
    assert mario.get("/tickets-tablero").status_code == 403  # un líder no lo tiene por su rol
    m = db.scalar(select(Usuario).where(Usuario.email == "mpineda@onecore.mx"))
    r = admin.post(f"/admin/usuarios/{m.id}", data={"nombre": m.nombre, "rol": m.rol, "area_id": m.area_id or "",
                                                     "menus_form": "1", "menus": ["inicio", "tickets", "tickets-tablero"]})
    assert r.status_code in (200, 303)
    db.refresh(m)
    assert m.menus_extra == ["tickets-tablero"]
    assert mario.get("/tickets-tablero").status_code == 200
    assert mario.get("/tickets-config").status_code == 403
    assert m in tk.asignables(db)


def test_analitica_y_diagnostico_por_area(admin, manuel, mario, db, fake):
    from app.models import TicketDiagnostico
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    jm = db.scalar(select(Usuario).where(Usuario.email == "jmartinez@onecore.mx"))
    t1 = tk.crear(db, m, _campos(referencia="1"), [], validacion="completo", faltantes=[], conversacion=[], prompt_version=None)
    t2 = tk.crear(db, m, _campos(referencia="2", producto="CIVA"), [], validacion="sin_ia", faltantes=[], conversacion=[],
                  prompt_version=None)
    tk.triage(db, jm, t1, "P1", jm)
    tk.cerrar(db, jm, t1, tipo_cierre="corregido", causa="Causa larga del problema", solucion="Solución aplicada en la validación")
    tk.triage(db, jm, t2, "P2", jm)
    db.commit()
    assert manuel.get("/tickets-analitica").status_code == 403
    assert mario.get("/tickets-analitica").status_code == 403
    html = admin.get("/tickets-analitica").text
    area = m.area.nombre
    assert "Analítica de tickets" in html and area in html and "Generar diagnóstico" in html
    assert "CIVA" in admin.get("/tickets-analitica?por=producto").text
    fake.respuestas = [{"general": {"salud": "amarillo", "resumen": "Ventas concentra los errores de SAFE.",
                                    "prioridades": ["Capacitar en carga de layout"]},
                        "grupos": [{"grupo": area, "salud": "rojo", "resumen": "Dos tickets de validación.",
                                    "patrones": ["Errores al cargar layout"], "riesgos": [], "recomendaciones": ["Regla de captura"]},
                                   {"grupo": "Inventado", "salud": "verde"}]}]
    admin.post("/tickets-analitica/diagnostico", data={"por": "area", "dias": 30})
    d = db.scalar(select(TicketDiagnostico))
    db.refresh(d)
    assert d.estado == "listo" and d.resultado["origen"] == "ia"
    assert [g["grupo"] for g in d.resultado["grupos"]] == [area] and d.resultado["grupos"][0]["salud"] == "rojo"
    assert "Errores al cargar layout" in fake.prompts[-1] or area in fake.prompts[-1]
    html = admin.get("/tickets-analitica").text
    assert "Ventas concentra los errores de SAFE." in html and "Regla de captura" in html
    # sin IA: diagnóstico por reglas
    fake.respuestas = [ia.IANoDisponible("caído")]
    fake.pedir_json = lambda *a, **k: (_ for _ in ()).throw(ia.IANoDisponible("caído"))
    admin.post("/tickets-analitica/diagnostico", data={"por": "producto", "dias": 30})
    d2 = db.scalar(select(TicketDiagnostico).where(TicketDiagnostico.dimension == "producto"))
    db.refresh(d2)
    assert d2.resultado["origen"] == "reglas" and {g["grupo"] for g in d2.resultado["grupos"]} == {"SAFE", "CIVA"}


def test_ia_en_vivo_usa_respuesta_estructurada(monkeypatch):
    import types
    import anthropic
    from app.services.ia import ClienteIA
    llamadas = []

    class Msgs:
        def create(self, **kw):
            llamadas.append(kw)
            bloque = types.SimpleNamespace(type="tool_use", input={"titulo": 'Patente "1686" no administrada', "faltan": [],
                                                                   "mensaje": "Listo"})
            return types.SimpleNamespace(content=[bloque], stop_reason="tool_use",
                                         usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: types.SimpleNamespace(messages=Msgs()))
    c = ClienteIA()
    c.s = types.SimpleNamespace(anthropic_api_key="x", ia_modelo_rapido="rapido")
    res, meta = c.pedir_json_vivo("p", esquema=tk.ESQUEMA_REVISION)
    assert res["titulo"] == 'Patente "1686" no administrada'
    assert llamadas[0]["tool_choice"] == {"type": "tool", "name": "respuesta"}
    assert llamadas[0]["tools"][0]["input_schema"]["properties"]["faltan"]["items"]["properties"]["campo"]["enum"][0] == "pantalla"

"""Sección Clientes (agenda, pendientes, benchmark), retiro de evidencias y fuentes del Círculo."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (
    AgendaCliente,
    Area,
    CirculoInnovacion,
    Compromiso,
    EvidenciaArchivo,
    InsightBenchmark,
    Mci,
    PendienteCliente,
    Predictiva,
    SesionCompetidor,
    Usuario,
)
from app.services import evidencias, innovacion
from app.services.util import hoy
from app.web.base import hash_password
from conftest import login

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

TRANS = ("Javier: Buenas tardes, empezamos con la revisión de pedimentos rectificados del mes. "
         "Laura del cliente: Tuvimos tres rectificaciones por error en la fracción arancelaria y nadie nos avisó a tiempo. "
         "Javier: Lo revisamos y les mandamos el detalle de cada caso a más tardar el viernes. "
         "Laura: También el reporte de descargos de octubre sale incompleto, le faltan las partidas del anexo 24. "
         "Javier: Nos comprometemos a corregir el reporte de descargos esta semana. "
         "Laura: Por lo demás el expediente en Smart Files nos ha ahorrado mucho tiempo en la auditoría. "
         "Pedro de OneCore: Perfecto, cerramos con los acuerdos y nos vemos el próximo mes para el resultado mensual. "
         ) * 2


def _u(db, email):
    return db.scalar(select(Usuario).where(Usuario.email == email))


def _comp_cumplido(db, admin):
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.area_id == a.id))
    jm = _u(db, "jmartinez@onecore.mx")
    c = Compromiso(semana="2026-W45", area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                   texto="Enviar a 5 clientes el reporte de cumplimiento", fecha_limite=hoy(), estado="abierto")
    db.add(c)
    db.commit()
    return c


def test_retirar_evidencia_conserva_historial_y_vuelve_a_verificar(admin, db, fake):
    c = _comp_cumplido(db, admin)
    fake.respuestas = [{"veredicto": "no_corresponde", "motivo": "Es la factura de otro cliente", "falta": ["el reporte"]}]
    admin.post(f"/compromisos/{c.id}/cumplido", data={"comentario": "listo"}, files={"archivos": ("otra.png", PNG, "image/png")})
    db.expire_all()
    c = db.get(Compromiso, c.id)
    assert c.cierre_veredicto == "no_corresponde"
    ev = c.evidencias[0]
    html = admin.get(f"/compromisos/{c.id}").text
    assert f"/evidencias/{ev.id}/retirar" in html and "Retirar" in html
    # sin motivo no se retira
    admin.post(f"/evidencias/{ev.id}/retirar", data={"motivo": ""})
    db.expire_all()
    assert not db.get(EvidenciaArchivo, ev.id).retirada
    admin.post(f"/evidencias/{ev.id}/retirar", data={"motivo": "Se subió la factura de otro cliente"})
    db.expire_all()
    c = db.get(Compromiso, c.id)
    ev = db.get(EvidenciaArchivo, ev.id)
    assert ev.retirada and ev.retirada_motivo.startswith("Se subió") and c.evidencias == []
    assert c.cierre_veredicto == "sin_verificar" and "Sube la evidencia correcta" in c.cierre_detalle["error"]
    assert c.cierre_detalle["historial"][-1]["veredicto"] == "no_corresponde"  # el diagnóstico previo no se pierde
    html = admin.get(f"/compromisos/{c.id}").text
    assert "Evidencias retiradas (1)" in html and "Revisiones anteriores" in html
    # la nueva evidencia se verifica sola y quita la sospecha
    fake.respuestas = [{"veredicto": "concluido", "motivo": "El reporte corresponde"}]
    admin.post(f"/compromisos/{c.id}/evidencias", files={"archivos": ("reporte.png", PNG, "image/png")})
    db.expire_all()
    c = db.get(Compromiso, c.id)
    assert c.cierre_veredicto == "concluido" and [e.nombre for e in c.evidencias] == ["reporte.png"]
    assert "error" not in c.cierre_detalle
    # el diagnóstico solo ve la evidencia vigente
    assert "reporte.png" in str(fake.prompts[-1]) and "otra.png" not in str(fake.prompts[-1])


def _agenda(admin, db, **kw):
    datos = {"tipo": "operativa", "fecha": hoy().isoformat(), "hora": "10:00", "responsable_id": _u(db, "egarza@onecore.mx").id,
             "cliente_nombre": "Manufacturas del Norte", "puntos": "Revisión de pedimentos rectificados\nAvance de Smart Files\nCapacitación de Anexo 30",
             "p_nombre": ["Laura Díaz", "Pedro Ruiz"], "p_email": ["laura@cliente.mx", ""], "p_lado": ["cliente", "onecore"]}
    datos.update(kw)
    r = admin.post("/clientes/agenda", data=datos, follow_redirects=False)
    assert r.status_code == 303
    return db.scalar(select(AgendaCliente).order_by(AgendaCliente.id.desc()))


def test_agenda_transcripcion_confirma_ejecucion_y_propone_pendientes(admin, db, fake):
    a = _agenda(admin, db)
    assert a.mes == hoy().strftime("%Y-%m") and len(a.puntos) == 3 and a.estado == "programada"
    html = admin.get(f"/clientes/agenda?mes={a.mes}").text
    assert "Manufacturas del Norte" in html and ("Programada" in html or "Hoy" in html)
    fake.respuestas = [{
        "evidencia": {"corresponde": True, "motivo": "Es la sesión operativa del cliente"}, "resumen": "Sesión operativa.",
        "riesgo_baja": "medio", "sentimiento": "neutral",
        "puntos": [{"n": 1, "cubierto": "si", "nota": "Se revisaron las tres", "cita": "Tuvimos tres rectificaciones por error en la fracción arancelaria"},
                   {"n": 2, "cubierto": "parcial", "nota": "Solo comentario"}, {"n": 3, "cubierto": "no"}],
        "participantes": [{"nombre": "Laura Díaz", "lado": "cliente"}, {"nombre": "Javier", "lado": "onecore"}],
        "hallazgos": [{"cuadrante": "servicio", "tema": "Avisos de rectificación", "cita": "nadie nos avisó a tiempo",
                       "sentimiento": "negativo", "severidad": "alta", "tipo": "queja"}],
        "pendientes": [{"tipo": "queja", "titulo": "Avisar a tiempo las rectificaciones", "detalle": "Tres casos sin aviso",
                        "severidad": "alta", "cita": "Tuvimos tres rectificaciones por error en la fracción arancelaria y nadie nos avisó a tiempo"},
                       {"tipo": "tarea", "titulo": "Corregir el reporte de descargos", "severidad": "media",
                        "cita": "Nos comprometemos a corregir el reporte de descargos esta semana"},
                       {"tipo": "tarea", "titulo": "Inventada", "cita": "esto nunca se dijo en la junta con el cliente"}],
        "acuerdos": [{"quien": "nosotros", "texto": "Mandar detalle de casos", "fecha": ""}]}]
    r = admin.post(f"/clientes/agenda/{a.id}/transcripcion", data={"consentimiento": "true"},
                   files={"archivo": ("t.txt", TRANS.encode(), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    a = db.get(AgendaCliente, a.id)
    assert a.sesion and a.sesion.tipo == "agenda" and a.sesion.estado == "analizada"
    assert a.estado == "con_huecos" and a.ejecucion["cubiertos"] == 1 and a.ejecucion["no_cubiertos"] == 1
    laura = next(i for i in a.invitados if i["nombre"] == "Laura Díaz")
    assert laura["asistio"] and laura["detectado"]
    assert {x["nombre"] for x in a.ejecucion["no_invitados"]} == {"Javier"}
    props = [p for p in a.pendientes if p.estado == "propuesta"]
    assert {p.titulo for p in props} == {"Avisar a tiempo las rectificaciones", "Corregir el reporte de descargos"}  # sin cita, fuera
    assert a.sesion.hallazgos and a.sesion.hallazgos[0].tema == "Avisos de rectificación"  # alimenta Voz del Cliente
    html = admin.get(f"/clientes/agenda/{a.id}").text
    assert "Ejecutada con huecos" in html or "no se cubri" in html
    assert "Propuesta de" in html and "Confirmar" in html

    # confirmar → pasa al tablero de Operaciones
    q = next(p for p in props if p.tipo == "queja")
    erick = _u(db, "egarza@onecore.mx")
    admin.post(f"/clientes/pendientes/{q.id}/confirmar", data={"asignado_id": erick.id, "fecha_compromiso": (hoy() + timedelta(days=3)).isoformat()})
    db.expire_all()
    q = db.get(PendienteCliente, q.id)
    assert q.estado == "pendiente" and q.asignado_id == erick.id
    kan = admin.get("/clientes/pendientes").text
    assert q.folio in kan and "Por atender" in kan and "Propuestas de" in kan
    # mover por JSON (drag & drop)
    d = admin.post(f"/clientes/pendientes/{q.id}/mover", data={"estado": "en_curso"}, headers={"Accept": "application/json"}).json()
    assert d["ok"]
    d = admin.post(f"/clientes/pendientes/{q.id}/mover", data={"estado": "resuelto"}, headers={"Accept": "application/json"}).json()
    assert not d["ok"] and d["resolver"]
    # resolver pide evidencia
    admin.post(f"/clientes/pendientes/{q.id}/resolver", data={"resolucion": "Se configuró aviso automático por correo"})
    db.expire_all()
    assert db.get(PendienteCliente, q.id).estado == "en_curso"
    fake.respuestas = [{"veredicto": "dudoso", "motivo": "La captura no muestra el aviso", "falta": ["correo enviado"]}]
    admin.post(f"/clientes/pendientes/{q.id}/resolver", data={"resolucion": "Se configuró aviso automático por correo"},
               files={"archivos": ("aviso.png", PNG, "image/png")})
    db.expire_all()
    q = db.get(PendienteCliente, q.id)
    assert q.estado == "resuelto" and q.veredicto == "dudoso"
    assert "Sospecha" in admin.get("/clientes/pendientes").text
    # retirar la evidencia equivocada: queda en historial y vuelve a esperar evidencia
    ev = q.evidencias[0]
    admin.post(f"/clientes/pendientes/archivos/{ev.id}/retirar", data={"motivo": "Captura equivocada"})
    db.expire_all()
    q = db.get(PendienteCliente, q.id)
    assert q.evidencias == [] and len(q.evidencias_retiradas) == 1 and q.veredicto == "sin_verificar"
    fake.respuestas = [{"veredicto": "concluido", "motivo": "Se ve el correo de aviso"}]
    admin.post(f"/clientes/pendientes/{q.id}/evidencias", files={"archivos": ("correo.png", PNG, "image/png")})
    db.expire_all()
    q = db.get(PendienteCliente, q.id)
    assert q.veredicto == "concluido" and q.verificacion["historial"]
    # descartar una propuesta pide motivo y no la borra
    t = next(p for p in props if p.tipo == "tarea")
    admin.post(f"/clientes/pendientes/{t.id}/descartar", data={"motivo": "Ya se atendió en la junta"})
    db.expire_all()
    assert db.get(PendienteCliente, t.id).estado == "descartado"


def test_agenda_reprogramar_cancelar_y_copiar_mes(admin, db):
    a = _agenda(admin, db, fecha=(hoy() + timedelta(days=2)).isoformat())
    admin.post(f"/clientes/agenda/{a.id}/reprogramar", data={"fecha": (hoy() + timedelta(days=4)).isoformat(), "motivo": "Cierre de mes del cliente"})
    db.expire_all()
    a = db.get(AgendaCliente, a.id)
    assert a.estado == "reprogramada" and any("Cierre de mes" in h["texto"] for h in a.historial)
    assert a.avisos[-1]["tipo"] == "reprogramacion"
    # no se puede cargar la transcripción antes de la sesión
    admin.post(f"/clientes/agenda/{a.id}/transcripcion", data={"consentimiento": "true"}, files={"archivo": ("t.txt", TRANS.encode(), "text/plain")})
    db.expire_all()
    assert db.get(AgendaCliente, a.id).sesion_id is None
    sig = (hoy().replace(day=1) + timedelta(days=32)).strftime("%Y-%m")
    admin.post("/clientes/agenda/copiar", data={"mes": sig})
    nuevas = list(db.scalars(select(AgendaCliente).where(AgendaCliente.mes == sig)))
    assert len(nuevas) == 1 and nuevas[0].puntos == a.puntos and not any(i["asistio"] for i in nuevas[0].invitados)
    admin.post(f"/clientes/agenda/{a.id}/cancelar", data={"motivo": "El cliente pidió posponer"})
    db.expire_all()
    assert db.get(AgendaCliente, a.id).estado == "cancelada"


def test_benchmark_metodologia_y_fuentes_del_circulo(admin, db, fake):
    tx = ("Competidor: Nosotros validamos el pedimento contra el SAT en línea y damos tablero de semáforo por aduana. "
          "No tenemos expediente documental, eso lo dejan al agente aduanal. Cobramos por pedimento y no por usuario. "
          "Además ofrecemos asesoría legal incluida para auditorías de comercio exterior. ") * 4
    fake.respuestas = [{
        "resumen": "Compiten en validación previa.", "propuesta_competidor": "Validación en línea con semáforo",
        "perfil": {"trabajos": [{"texto": "Pagar pedimentos sin errores", "onecore": "si"}], "dolores": [], "ganancias": []},
        "mapa": {"productos": [{"texto": "Semáforo por aduana", "onecore": "no"}], "aliviadores": [], "creadores": []},
        "factores": [{"factor": "Validación previa", "competidor": 4, "onecore": 5, "nota": "Ambos validan"},
                     {"factor": "Expediente documental", "competidor": 1, "onecore": 5, "nota": "Ellos no tienen"},
                     {"factor": "Asesoría legal", "competidor": 4, "onecore": 2, "nota": "La incluyen"}],
        "eric": {"crear": [{"titulo": "Semáforo por aduana", "detalle": "Tablero de riesgo", "cita": "damos tablero de semáforo por aduana"}],
                 "incrementar": [], "reducir": [], "eliminar": []},
        "insights": [{"tipo": "nuestro", "titulo": "Expediente documental", "impacto": "alta",
                      "cita": "No tenemos expediente documental, eso lo dejan al agente aduanal"},
                     {"tipo": "oportunidad", "titulo": "Asesoría legal en auditorías", "impacto": "alta",
                      "cita": "ofrecemos asesoría legal incluida para auditorías de comercio exterior"},
                     {"tipo": "suyo", "titulo": "Sin cita real", "cita": "esto no aparece en ningún lado de la transcripción"}],
        "recomendacion": {"modificar": True, "que": "Sumar semáforo por aduana a SAFE", "porque": "Lo valoran"}}]
    r = admin.post("/clientes/benchmark/sesiones", data={"competidor_nuevo": "Competidor Uno", "fecha": hoy().isoformat(),
                                                         "titulo": "Demo"},
                   files=[("archivo", ("t.txt", tx.encode(), "text/plain")), ("pantallas", ("pant.png", PNG, "image/png"))],
                   follow_redirects=False)
    assert r.status_code == 303
    s = db.scalar(select(SesionCompetidor))
    db.refresh(s)
    assert s.estado == "analizada" and s.cifrada and len(s.pantallas) == 1
    assert {i.tipo for i in s.insights} == {"nuestro", "oportunidad", "crear"}  # el insight sin cita se descarta
    assert any(b.get("type") == "image" for b in fake.prompts[-1] if isinstance(b, dict))  # las pantallas van a la IA
    html = admin.get(f"/clientes/benchmark/sesiones/{s.id}").text
    assert "Lienzo de propuesta de valor" in html and "<polyline" in html and "Matriz ERIC" in html and "Semáforo por aduana" in html
    panel = admin.get("/clientes/benchmark").text
    assert "Competidor Uno" in panel and "<polyline" in panel and "Asesoría legal en auditorías" in panel
    assert "semáforo por aduana" in admin.get(f"/clientes/benchmark/sesiones/{s.id}/transcripcion").text

    # una queja confirmada y la oportunidad del benchmark llegan al Círculo con su fuente enlazada
    q = PendienteCliente(tipo="queja", titulo="Avisar rectificaciones", cita="nadie nos avisó", estado="pendiente",
                         cliente_nombre="Cliente X", historial=[])
    db.add(q)
    db.commit()
    f = innovacion.fuentes(db)
    op = db.scalar(select(InsightBenchmark).where(InsightBenchmark.tipo == "oportunidad"))
    assert q in f["quejas"] and op in f["benchmark"]
    c = CirculoInnovacion(mes=hoy().strftime("%Y-%m"), nombre="Círculo prueba")
    db.add(c)
    db.commit()
    fake.respuestas = [{"lectura": "Hay dos frentes.", "iniciativas": [
        {"titulo": "Servicio de asesoría en auditorías", "descripcion": "Paquete", "impacto": 4, "esfuerzo": 2,
         "fuentes": {"quejas": [f"Q{q.id}"], "benchmark": [f"B{op.id}"]}, "cita": f"B{op.id}"}], "sueltas": []}]
    innovacion.consolidar(db, c, _u(db, "jmartinez@onecore.mx"))
    db.refresh(c)
    ini = c.iniciativas[0]
    assert ini.fuentes["quejas"] == [q.id] and ini.fuentes["benchmark"] == [op.id]
    assert "Q" + str(q.id) in str(fake.prompts[-1]) and "Competidor Uno" in str(fake.prompts[-1])
    html = admin.get(f"/circulo/{c.id}/fuentes/{ini.id}").text
    assert f"/clientes/pendientes/{q.id}" in html and f"/clientes/benchmark/sesiones/{s.id}" in html
    assert f"/clientes/benchmark/sesiones/{s.id}/transcripcion" in html


def test_menus_de_clientes_por_rol(db):
    a = db.scalar(select(Area).where(Area.clave == "operaciones"))
    db.add(Usuario(nombre="Ana Ops", email="ana@onecore.mx", rol="colaborador", area_id=a.id, activo=True,
                   password_hash=hash_password("clave-segura-123")))
    db.commit()
    ana = login(TestClient(app), "ana@onecore.mx")
    assert ana.get("/clientes/agenda").status_code == 200
    assert ana.get("/clientes/pendientes").status_code == 200
    assert ana.get("/clientes/benchmark").status_code == 403
    # un colaborador captura un pendiente: queda como propuesta hasta que un líder lo confirme
    ana.post("/clientes/pendientes", data={"titulo": "Revisar acceso de usuario del cliente", "tipo": "tarea", "cliente_nombre": "ACME"})
    p = db.scalar(select(PendienteCliente))
    assert p.estado == "propuesta" and p.asignado_id is None
    assert ana.post(f"/clientes/pendientes/{p.id}/confirmar", data={"asignado_id": 1, "fecha_compromiso": hoy().isoformat()}).status_code == 403
    # el nombre del cliente es confidencial: quien no lo atiende ve una etiqueta
    p.creado_por = None
    db.commit()
    assert "ACME" not in ana.get("/clientes/pendientes").text


def test_avisos_internos_de_agenda_invitacion_recordatorio_y_escalacion(admin, db, monkeypatch):
    from app.services import agenda_avisos, correo
    enviados = []
    monkeypatch.setattr(correo, "enviar", lambda para, asunto, cuerpo, enlace=None, **kw: enviados.append(
        {"para": para, "asunto": asunto, "cuerpo": cuerpo, **kw}) or "simulado")
    mario = _u(db, "mpineda@onecore.mx")
    mario.puesto = "customer_success"
    dg, dc = _u(db, "mcoronado@onecore.mx"), _u(db, "gtrevino@onecore.mx")
    dg.puesto, dc.puesto = "director_general", "director_comercial"
    db.commit()
    a = _agenda(admin, db, fecha=(hoy() + timedelta(days=12)).isoformat(),
                p_nombre=["Laura Díaz", "Pedro Ruiz"], p_email=["laura@cliente.mx", "pedro@onecore.mx"], p_lado=["cliente", "onecore"])
    inv = enviados[-1]
    assert set(inv["para"]) == {"egarza@onecore.mx", "mpineda@onecore.mx", "pedro@onecore.mx"}  # nunca al cliente
    assert "laura@cliente.mx" not in str(inv["para"]) + str(inv.get("cc"))
    metodo, ics = inv["calendario"]
    assert metodo == "REQUEST" and f"UID:agenda-{a.id}@mci.onecore.mx" in ics and "TZID=America/Mexico_City" in ics
    assert "OBLIGATORIA" in inv["cuerpo"] and inv["asunto"].startswith("[VERDE]") and not inv["cc"]
    # Customer Success apaga sus avisos desde su perfil
    cs = login(TestClient(app), "mpineda@onecore.mx")
    cs.post("/perfil/avisos", data={})
    db.expire_all()
    assert _u(db, "mpineda@onecore.mx").recibe_agenda is False
    # un pendiente vencido del mismo cliente pone la bandera en rojo
    db.add(PendienteCliente(tipo="queja", titulo="Reporte de descargos incompleto", estado="en_curso", cliente_nombre="Manufacturas del Norte",
                            asignado_id=_u(db, "egarza@onecore.mx").id, fecha_compromiso=hoy() - timedelta(days=3), historial=[]))
    a = db.get(AgendaCliente, a.id)
    a.fecha = hoy() + timedelta(days=4)
    a.avisos[0]["en"] = (hoy() - timedelta(days=10)).isoformat() + "T15:00:00"
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(a, "avisos")
    db.commit()
    assert agenda_avisos.recordatorios(db) == 1
    rec = enviados[-1]
    assert rec["asunto"].startswith("[ROJO]") and "calendario" in rec and rec["calendario"] is None
    assert set(rec["cc"]) == {"mcoronado@onecore.mx", "gtrevino@onecore.mx"} and "mpineda@onecore.mx" not in rec["para"]
    assert "PENDIENTES VENCIDOS" in rec["cuerpo"] and "Reporte de descargos incompleto" in rec["cuerpo"]
    assert agenda_avisos.recordatorios(db) == 0  # uno por fecha
    html = admin.get(f"/clientes/agenda/{a.id}").text
    assert "¿Vamos preparados?" in html and "flag-prep rojo" in html and "Recordatorio 5 días antes" in html
    assert "flag-prep rojo" in admin.get(f"/clientes/agenda?mes={a.fecha:%Y-%m}").text
    # cancelar quita la cita del calendario
    admin.post(f"/clientes/agenda/{a.id}/cancelar", data={"motivo": "El cliente pidió posponer"})
    assert enviados[-1]["calendario"][0] == "CANCEL" and "STATUS:CANCELLED" in enviados[-1]["calendario"][1]

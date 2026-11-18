"""Citas recurrentes, delegación de pendientes a soporte, revisión del solicitante, buscadores y recurrencias de servicio."""
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AgendaCliente, AnalisisServicio, PendienteCliente, Ticket, Usuario
from app.services import clientes as cl
from app.services import tickets as tk
from app.services.util import hoy
from conftest import login


def _u(db, email):
    return db.scalar(select(Usuario).where(Usuario.email == email))


def _datos(db, **kw):
    d = {"tipo": "operativa", "fecha": (hoy() + timedelta(days=3)).isoformat(), "hora": "10:00",
         "responsable_id": _u(db, "egarza@onecore.mx").id, "cliente_nombre": "Manufacturas del Norte",
         "puntos": "Revisión de pedimentos", "p_nombre": ["Erick Garza"], "p_email": ["egarza@onecore.mx"], "p_lado": ["onecore"]}
    d.update(kw)
    return d


def test_fechas_de_serie_con_dias_habiles_y_festivos():
    f = lambda xs: [x[0] for x in xs]  # noqa: E731
    # cada 10: el 10/01/2027 es domingo → lunes 11; el 10/04/2027 es sábado → lunes 12
    r = cl.fechas_serie(date(2027, 1, 1), "mensual", date(2027, 4, 30), modo="dia", dia_mes=10)
    assert f(r) == [date(2027, 1, 11), date(2027, 2, 10), date(2027, 3, 10), date(2027, 4, 12)]
    assert "domingo" in r[0][1] and r[1][1] == ""
    # primer lunes: en febrero es festivo (Día de la Constitución) → martes
    r = cl.fechas_serie(date(2027, 1, 1), "mensual", date(2027, 3, 31), modo="nesimo", dia_semana=0, nesimo=1)
    assert f(r) == [date(2027, 1, 4), date(2027, 2, 2), date(2027, 3, 1)] and "Constitución" in r[1][1]
    # cada 16 de septiembre se recorre; el último viernes también funciona
    assert f(cl.fechas_serie(date(2027, 9, 1), "mensual", date(2027, 9, 30), dia_mes=16)) == [date(2027, 9, 17)]
    assert f(cl.fechas_serie(date(2027, 1, 1), "mensual", date(2027, 1, 31), modo="nesimo", dia_semana=4, nesimo=-1)) == [date(2027, 1, 29)]
    # semanal: cada miércoles a partir de la fecha de inicio
    r = cl.fechas_serie(date(2027, 1, 4), "semanal", date(2027, 1, 31), dia_semana=2)
    assert f(r) == [date(2027, 1, 6), date(2027, 1, 13), date(2027, 1, 20), date(2027, 1, 27)]
    assert cl.inhabil(date(2026, 12, 25)) == "Navidad" and cl.inhabil(date(2030, 10, 1))


def test_vista_previa_y_reprogramar_no_habil(admin, db):
    d = admin.get("/api/clientes/serie", params={"fecha": "2027-01-01", "recurrencia": "mensual", "fecha_fin": "2027-03-31",
                                                 "modo_mes": "dia", "dia_mes": 10}).json()
    assert d["ok"] and len(d["fechas"]) == 3 and d["fechas"][0]["nota"] and "cada día 10" in d["regla"]
    assert not admin.get("/api/clientes/serie", params={"fecha": "2027-01-01", "recurrencia": "mensual"}).json()["ok"]
    admin.post("/clientes/agenda", data=_datos(db))
    a = db.scalar(select(AgendaCliente))
    sabado = hoy() + timedelta(days=(5 - hoy().weekday()) % 7 or 7)
    admin.post(f"/clientes/agenda/{a.id}/reprogramar", data={"fecha": sabado.isoformat(), "motivo": "Cliente pidió moverla"})
    db.refresh(a)
    assert a.estado == "programada"  # no se reprograma a un día inhábil


def test_citas_recurrentes_cada_una_con_su_transcripcion(admin, db, monkeypatch):
    from app.services import agenda_avisos
    enviados = []
    monkeypatch.setattr(agenda_avisos, "enviar_en_fondo", lambda aid, tipo: enviados.append((aid, tipo)))
    ini = hoy() + timedelta(days=3)
    admin.post("/clientes/agenda", data=_datos(db, fecha=ini.isoformat(), recurrencia="mensual", modo_mes="dia",
                                               dia_mes=str(ini.day), fecha_fin=(ini + timedelta(days=150)).isoformat()))
    serie = list(db.scalars(select(AgendaCliente).order_by(AgendaCliente.fecha)))
    assert len(serie) == 5 and len({a.serie_id for a in serie}) == 1 and all(a.recurrencia == "mensual" for a in serie)
    assert len({a.mes for a in serie}) == 5  # una por mes, visibles en la agenda de cada mes
    assert all(not cl.inhabil(a.fecha) for a in serie)  # siempre en día hábil
    # invitación de calendario solo para las que están a 35 días o menos; las demás las manda el proceso diario
    assert {aid for aid, _ in enviados} == {a.id for a in serie if (a.fecha - hoy()).days <= 35}
    html = admin.get(f"/clientes/agenda/{serie[2].id}").text
    assert "Serie mensual · cita 3 de 5" in html
    # sin fecha fin no se crea la serie; más de 12 meses tampoco
    n = db.query(AgendaCliente).count()
    admin.post("/clientes/agenda", data=_datos(db, recurrencia="semanal"))
    admin.post("/clientes/agenda", data=_datos(db, recurrencia="semanal", fecha_fin=(hoy() + timedelta(days=500)).isoformat()))
    assert db.query(AgendaCliente).count() == n
    # cancelar esta y las siguientes
    admin.post(f"/clientes/agenda/{serie[2].id}/cancelar", data={"motivo": "El cliente cambió de proveedor", "serie": "true"})
    db.expire_all()
    assert [a.estado for a in db.scalars(select(AgendaCliente).order_by(AgendaCliente.fecha))] == \
        ["programada", "programada", "cancelada", "cancelada", "cancelada"]


def test_pendiente_delegado_a_soporte_avanza_con_el_ticket(admin, db):
    jm, erick = _u(db, "jmartinez@onecore.mx"), _u(db, "egarza@onecore.mx")
    p = PendienteCliente(tipo="queja", titulo="Error de sello al timbrar en CIVA", detalle="Todas las facturas del día",
                         cita="no nos deja timbrar ninguna factura", severidad="alta", cliente_nombre="ACME", estado="en_curso",
                         asignado_id=erick.id, fecha_compromiso=hoy() + timedelta(days=3), producto="CIVA", historial=[])
    db.add(p)
    db.commit()
    html = admin.get(f"/clientes/pendientes/{p.id}").text
    assert f"/tickets/nuevo?pendiente={p.id}" in html and "Delegar a soporte" in html
    form = admin.get(f"/tickets/nuevo?pendiente={p.id}").text
    assert "Error de sello al timbrar en CIVA" in form and "no nos deja timbrar" in form and 'value="ACME"' in form
    assert f'name="pendiente_id" value="{p.id}"' in form and '<option value="CIVA" selected' in form
    erick_c = login(TestClient(app), "egarza@onecore.mx")  # quien atiende el pendiente lo delega
    r = erick_c.post("/api/tickets/nuevo", data={"descripcion": "Error de sello al timbrar en CIVA. Todas las facturas del día",
                                               "producto": "CIVA", "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: sello",
                                               "validacion": "completo", "pendiente_id": str(p.id)}).json()
    assert r["ok"] and r["url"] == f"/clientes/pendientes/{p.id}"
    db.expire_all()
    p = db.get(PendienteCliente, p.id)
    t = p.ticket
    assert t and p.estado == "en_curso"  # soporte aún no lo acepta: sigue en pendientes
    assert "espera que lo acepten" in admin.get(f"/clientes/pendientes/{p.id}").text
    # triage = soporte lo acepta → delegado
    admin.post(f"/tickets-triage/{t.id}", data={"prioridad": "P2", "asignado_id": jm.id})
    db.expire_all()
    p = db.get(PendienteCliente, p.id)
    assert p.estado == "delegado"
    kan = admin.get("/clientes/pendientes").text
    assert "Delegado a soporte" in kan and t.folio in kan
    # planear y resolver: pasa a revisión de quien levantó (admin) y el pendiente sigue delegado
    t = db.get(Ticket, t.id)
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "3", "fecha_entrega": date.today().isoformat(), "asignado_id": jm.id})
    admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "corregido", "causa": "El certificado del cliente venció",
                                                     "solucion": "Se cargó el certificado vigente y se reintentó el timbrado"})
    db.expire_all()
    t, p = db.get(Ticket, t.id), db.get(PendienteCliente, p.id)
    assert t.estado == "revision" and p.estado == "delegado"
    # el solicitante (quien delegó) acepta → ticket cerrado → el pendiente regresa a revisión con la solución
    erick_c.post(f"/tickets/{t.id}/aceptar", data={})
    db.expire_all()
    t, p = db.get(Ticket, t.id), db.get(PendienteCliente, p.id)
    assert t.estado == "cerrado" and p.estado == "revision" and "certificado vigente" in p.resolucion
    # se resuelve sin subir archivo: la evidencia es el ticket cerrado
    admin.post(f"/clientes/pendientes/{p.id}/resolver", data={"resolucion": "Confirmado con el cliente: ya timbra"})
    db.expire_all()
    p = db.get(PendienteCliente, p.id)
    assert p.estado == "resuelto" and p.veredicto == "soporte"


def test_revision_del_solicitante_y_recordatorio(admin, db, monkeypatch):
    from app.services import correo
    enviados = []
    monkeypatch.setattr(correo, "enviar", lambda para, asunto, cuerpo, enlace=None, **kw: enviados.append(
        {"para": para, "asunto": asunto, "cuerpo": cuerpo, **kw}) or "simulado")
    jm, manuel, mario = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx"), _u(db, "mpineda@onecore.mx")
    manuel.jefe_id = mario.id
    db.commit()
    t = tk.crear(db, manuel, {"descripcion": "Al timbrar la factura en CIVA sale error de sello en todas", "producto": "CIVA",
                              "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: sello"}, [], validacion="completo",
                 faltantes=[], conversacion=[], prompt_version=None)
    tk.triage(db, jm, t, "P2", jm)
    tk.planear(db, jm, t, horas=2, fecha_entrega=date.today(), asignado=jm)
    db.commit()
    # resolver exige base de conocimiento
    admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "corregido", "causa": "x", "solucion": "y"})
    db.refresh(t)
    assert t.estado == "planeado"
    enviados.clear()
    admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "corregido", "causa": "El certificado del cliente venció",
                                                  "solucion": "Se cargó el certificado vigente y se reintentó", "modulos": "civa/timbrado.py"})
    db.refresh(t)
    assert t.estado == "revision" and t.resolucion["modulos"] == "civa/timbrado.py"
    aviso = next(e for e in enviados if "revísalo" in e["asunto"])
    assert "mcoronado@onecore.mx" in aviso["para"] and "mpineda@onecore.mx" in aviso["para"]  # solicitante y su jefe
    # el ingeniero no puede cerrarlo por su cuenta ni moverlo: lo revisa quien lo levantó
    eng = login(TestClient(app), "egarza@onecore.mx")
    eng.post(f"/tickets/{t.id}/aceptar")
    db.refresh(t)
    assert t.estado == "revision"
    html = login(TestClient(app), "mcoronado@onecore.mx").get(f"/tickets/{t.id}").text
    assert "te toca revisarlo" in html and "Aceptar y cerrar" in html
    tab = admin.get("/tickets-tablero").text
    assert "Recordar revisión" in tab and "Revisa: Manuel" in tab
    # Recordar revisión: tabla a solicitante y a su jefe
    enviados.clear()
    admin.post("/tickets-revision/recordar")
    a_quien = {e["para"][0] for e in enviados}
    assert a_quien == {"mcoronado@onecore.mx", "mpineda@onecore.mx"}
    tabla = enviados[0]["tabla"]
    assert tabla["columnas"][2] == "Días desde que se resolvió" and tabla["filas"][0][0] == t.folio
    assert tabla["filas"][0][3] == jm.nombre and tabla["filas"][0][4].endswith(f"/tickets/{t.id}")
    # rechaza con motivo → vuelve a En curso; acepta → cerrado con tarifa fija
    mc = login(TestClient(app), "mcoronado@onecore.mx")
    mc.post(f"/tickets/{t.id}/rechazar", data={"motivo": "Sigue saliendo el error en la sucursal 2"})
    db.refresh(t)
    assert t.estado == "en_curso" and t.resolucion["rechazos"][0]["motivo"].startswith("Sigue")
    admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "corregido", "causa": "Faltaba el certificado de la sucursal 2",
                                                  "solucion": "Se cargó también el certificado de la sucursal 2"})
    mc.post(f"/tickets/{t.id}/aceptar", data={"comentario": "Ya funciona"})
    db.refresh(t)
    assert t.estado == "cerrado" and t.resolucion["aceptado_por"] == manuel.id


def test_buscadores_de_kanban_y_horas_en_triage(admin, db):
    jm = _u(db, "jmartinez@onecore.mx")
    abierto = tk.crear(db, jm, {"descripcion": "Reporte de descargos incompleto en anexo 24 del cliente", "producto": "CIVA",
                                "tipo": "error", "impacto": "afecta", "titulo": "Descargos incompletos"}, [],
                       validacion="completo", faltantes=[], conversacion=[], prompt_version=None)
    cerrado = tk.crear(db, jm, {"descripcion": "Descargos duplicados en el reporte mensual del cliente", "producto": "CIVA",
                                "tipo": "error", "impacto": "afecta", "titulo": "Descargos duplicados"}, [],
                       validacion="completo", faltantes=[], conversacion=[], prompt_version=None)
    tk.triage(db, jm, abierto, "P3", jm)
    tk.planear(db, jm, abierto, horas=6, fecha_entrega=date.today(), asignado=jm)
    tk.triage(db, jm, cerrado, "P3", jm)
    cerrado.estado, cerrado.cerrado_en = "cerrado", tk.utcnow() - timedelta(days=40)
    db.commit()
    html = admin.get("/tickets-tablero?buscar=descargos").text
    assert abierto.folio in html and cerrado.folio not in html
    html = admin.get("/tickets-tablero?buscar=descargos&cerrados=true").text
    assert abierto.folio in html and cerrado.folio in html
    assert abierto.folio in admin.get(f"/tickets-tablero?buscar={abierto.id}").text
    tri = admin.get("/tickets-triage").text
    assert "Horas est." in tri and "6.0 h" in tri and "Sin estimación" in tri
    # kanban de pendientes de clientes
    db.add_all([PendienteCliente(tipo="tarea", titulo="Capacitar en anexo 30", estado="pendiente", cliente_nombre="A", historial=[]),
                PendienteCliente(tipo="tarea", titulo="Capacitar en MVE", estado="resuelto", cliente_nombre="A", historial=[],
                                 resuelto_en=tk.utcnow() - timedelta(days=60))])
    db.commit()
    k = admin.get("/clientes/pendientes?q=capacitar").text
    assert "Capacitar en anexo 30" in k and "Capacitar en MVE" not in k
    assert "Capacitar en MVE" in admin.get("/clientes/pendientes?q=capacitar&cerrados=true").text


def test_recurrencias_de_servicio(admin, db, fake):
    a = AgendaCliente(mes=hoy().strftime("%Y-%m"), tipo="operativa", area_id=1, responsable_id=1, cliente_nombre="B",
                      fecha=hoy(), puntos=["x"], invitados=[], estado="realizada", creado_por=1, historial=[],
                      ejecucion={"minuta": {"riesgos": [{"texto": "Multas por rectificaciones sin aviso", "cita": ""}],
                                            "acuerdos": [], "temas_abiertos": [], "decisiones": []}, "puntos": []})
    db.add(a)
    ps = [PendienteCliente(tipo="queja", titulo="No avisan rectificaciones", estado="pendiente", cliente_nombre=c,
                           severidad="alta", historial=[]) for c in ("A", "C")]
    db.add_all(ps)
    db.commit()
    fake.respuestas = [{"lectura": "Las rectificaciones sin aviso son lo más grave.", "temas": [
        {"titulo": "Avisos de rectificación", "categoria": "queja", "modulo": "SAFE", "causa_raiz": "No hay alerta automática",
         "impacto": 5, "impacto_por": "Multas", "fuentes": [f"P{ps[0].id}", f"P{ps[1].id}", f"M{a.id}-1", "P9999"],
         "plan": [{"accion": "Crear alerta automática de rectificación", "tipo": "preventiva", "area": "Producto",
                   "plazo_dias": 30, "indicador": "0 rectificaciones sin aviso"}]},
        {"titulo": "Tema sin fuentes reales", "impacto": 2, "fuentes": ["P9999"]}]}]
    r = admin.post("/clientes/servicio", data={"desde": (hoy() - timedelta(days=30)).isoformat(), "hasta": hoy().isoformat()},
                   follow_redirects=False)
    assert r.status_code == 303
    an = db.scalar(select(AnalisisServicio))
    db.refresh(an)
    temas = an.resultado["temas"]
    assert len(temas) == 1 and temas[0]["clientes"] == 3 and temas[0]["menciones"] == 3  # la fuente inventada no cuenta
    assert temas[0]["cuadrante"] == "atacar"
    html = admin.get("/clientes/servicio").text
    assert "Frecuencia × impacto" in html and "Atacar ya" in html and "Crear alerta automática de rectificación" in html
    assert f"/clientes/pendientes/{ps[0].id}" in html and f"/clientes/agenda/{a.id}" in html

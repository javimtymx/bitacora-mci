"""Cierres Cancelado/Aclaración, V°B° cliente, cargas y Gantt, pendientes con imágenes/anexos, aclaración de
compromisos y cliente de la sesión de agenda."""
import io
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import AgendaCliente, ArchivoCliente, Compromiso, PendienteCliente, Ticket, TicketImagen, Usuario
from app.services import cargas as cg
from app.services import clientes as cl
from app.services import tickets as tk
from conftest import login
from test_evidencias_edicion import PNG, _comp


def _u(db, email):
    return db.scalar(select(Usuario).where(Usuario.email == email))


def _ticket(db, autor, titulo="CIVA: sello", prioridad="P2", horas=None, ing=None, entrega=None):
    jm = _u(db, "jmartinez@onecore.mx")
    t = tk.crear(db, autor, {"descripcion": f"{titulo}: al timbrar la factura sale error de sello en todas", "producto": "CIVA",
                             "tipo": "error", "impacto": "bloquea", "titulo": titulo}, [], validacion="completo",
                 faltantes=[], conversacion=[], prompt_version=None)
    tk.triage(db, jm, t, prioridad, ing or jm)
    if horas:
        tk.planear(db, jm, t, horas=horas, fecha_entrega=entrega or date.today() + timedelta(days=30), asignado=ing or jm)
    db.commit()
    return t


def test_cancelado_y_aclaracion_sin_causa_raiz(admin, db):
    manuel = _u(db, "mcoronado@onecore.mx")
    t = _ticket(db, manuel, horas=2)
    html = admin.get(f"/tickets/{t.id}").text
    assert 'value="cancelado"' in html and 'value="aclaracion"' in html
    r = admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "cancelado", "solucion": "corto", "horas_reales": "1"})
    db.refresh(t)
    assert t.estado != "revision"  # el motivo es obligatorio
    admin.post(f"/tickets/{t.id}/resolver", data={"tipo_cierre": "cancelado", "horas_reales": "1",
                                                  "solucion": "El cliente ya no usará el timbrado en CIVA este año"})
    db.refresh(t)
    assert t.estado == "revision" and t.resolucion["tipo"] == "cancelado" and not t.resolucion["causa"]
    html = admin.get(f"/tickets/{t.id}").text
    assert "Motivo" in html and "Cancelado" in html
    tk.aceptar(db, manuel, t)
    db.commit()
    assert all(s["id"] != t.id for s in tk.similares(db, t.titulo + " " + t.descripcion, minimo=0.01))


def test_vobo_cliente(admin, db):
    manuel = _u(db, "mcoronado@onecore.mx")
    jm = _u(db, "jmartinez@onecore.mx")
    t = _ticket(db, manuel, horas=2)
    tk.resolver(db, jm, t, tipo_cierre="corregido", causa="Certificado vencido del cliente",
                solucion="Se cargó el certificado vigente y se reintentó")
    db.commit()
    mc = login(TestClient(app), "mcoronado@onecore.mx")
    r = mc.post(f"/tickets/{t.id}/vobo", data={"nota": "Que el cliente valide el timbrado"}, headers={"Accept": "application/json"})
    assert r.json()["ok"]
    db.refresh(t)
    assert t.estado == "vobo" and tk.vence_en(t) == ("", "")
    assert "V°B° cliente" in admin.get("/tickets-tablero").text
    tabs = mc.get("/tickets?tab=vobo").text
    assert t.folio in tabs and "En revisión" in tabs and "Cerrados" in tabs and "Por cerrar" not in tabs
    assert f">{t.folio}<" not in mc.get("/tickets?tab=revision").text and f">{t.folio}<" in tabs
    assert mc.get("/tickets?tab=cerrar").status_code == 200  # ligas viejas
    # desde V°B° se cierra o regresa, no se mueve libre
    assert admin.post(f"/tickets/{t.id}/mover", data={"estado": "pendiente"}).status_code in (200, 303, 400)
    db.refresh(t)
    assert t.estado == "vobo"
    mc.post(f"/tickets/{t.id}/aceptar", data={"comentario": "El cliente confirmó"})
    db.refresh(t)
    assert t.estado == "cerrado"
    # en Mis tickets dice quién lo levantó
    assert "Levantó tú" in mc.get("/tickets?ver=empresa&tab=cerrados").text


def test_cargas_sugerencia_y_gantt(admin, db):
    manuel = _u(db, "mcoronado@onecore.mx")
    jm = _u(db, "jmartinez@onecore.mx")
    lunes = cg.siguiente_habil(date.today())
    a = _ticket(db, manuel, "A largo", "P3", horas=8, entrega=lunes)
    b = _ticket(db, manuel, "B medio", "P2", horas=4, entrega=lunes + timedelta(days=20))
    tramos, carga = cg.plan_ingeniero(db, jm)
    assert [tr.ticket.id for tr in tramos] == [b.id, a.id]  # P2 antes que P3
    assert all(h <= 4.0 for h in carga.values())
    assert tramos[0].inicio == lunes and tramos[0].fin == lunes  # 4 h en un día
    # nuevo P1 de 4 h: va primero y recorre a los demás
    n = _ticket(db, manuel, "N urgente", "P1")
    r = admin.get(f"/api/tickets/{n.id}/sugerencia?horas=4&asignado_id={jm.id}").json()
    assert r["ok"] and r["elegido"]["fecha"] == lunes.isoformat()
    folios = {x["folio"]: x for x in r["elegido"]["afectados"]}
    assert a.folio in folios and folios[a.folio]["tarde"]
    # planear confirmando el recorrido de A
    nueva = folios[a.folio]["fin_nuevo"]
    admin.post(f"/tickets/{n.id}/planear", data={"horas": "4", "fecha_entrega": lunes.isoformat(), "asignado_id": jm.id,
                                                 f"fecha_{a.id}": nueva, "motivo_carga": "Entró un P1 de CIVA"})
    db.refresh(a)
    assert a.fecha_entrega.isoformat() == nueva and "P1 de CIVA" in a.estimaciones[-1].motivo
    # vistas
    g = admin.get("/tickets-tablero?vista=gantt").text
    assert "gnt-bar" in g and n.folio in g
    c = admin.get(f"/tickets-tablero?vista=cargas&persona={jm.id}").text
    assert "Cola de" in c and a.folio in c
    # reordenar a mano: A primero
    admin.post("/tickets-cargas/orden", data={"persona": jm.id, "ids": f"{a.id},{n.id},{b.id}"})
    db.expire_all()
    assert [tr.ticket.id for tr in cg.plan_ingeniero(db, jm)[0]][0] == a.id
    # «no antes de» en día inhábil se rechaza
    sab = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
    admin.post("/tickets-cargas/inicio", data={"tid": b.id, "inicio": sab.isoformat()})
    db.refresh(b)
    assert b.inicio_plan is None


def test_pendiente_con_imagen_en_texto_y_anexos(admin, db):
    r = admin.post("/clientes/pendientes-img", files={"imagen": ("c.png", io.BytesIO(PNG), "image/png")}).json()
    assert r["ok"] and r["url"].startswith("/clientes/pendientes-img/")
    admin.post("/clientes/pendientes", data={"tipo": "queja", "titulo": "El reporte de descargos no cuadra",
                                             "detalle": f"Así lo ve el cliente:\n{r['md']}\nen octubre", "cliente_nombre": "Cliente X"},
               files=[("anexos", ("correo.txt", b"correo del cliente", "text/plain"))])
    p = db.scalar(select(PendienteCliente).order_by(PendienteCliente.id.desc()))
    assert db.get(TicketImagen, r["id"]).pendiente_id == p.id
    an = db.scalars(select(ArchivoCliente).where(ArchivoCliente.entidad == "pend_anexo", ArchivoCliente.entidad_id == p.id)).all()
    assert len(an) == 1 and not p.evidencias
    html = admin.get(f"/clientes/pendientes/{p.id}").text
    assert f'<img src="{r["url"]}"' in html and "correo.txt" in html
    assert admin.get(f"/clientes/pendientes/anexos/{an[0].id}").content == b"correo del cliente"


def test_aclarar_cierre_de_compromiso(manuel, db, fake):
    c = _comp(db)
    fake.respuestas = [{"veredicto": "dudoso", "motivo": "No se ven las 2 citas.", "falta": ["2 citas"],
                        "pregunta": "¿Dónde quedaron registradas las 2 citas?"}]
    manuel.post(f"/compromisos/{c.id}/cumplido", data={"comentario": "Envié la campaña"},
                files=[("archivos", ("captura.png", PNG, "image/png"))])
    db.expire_all()
    c = db.get(Compromiso, c.id)
    assert c.cierre_detalle["pregunta"].startswith("¿Dónde")
    t = manuel.get(f"/compromisos/{c.id}").text
    assert "pregunta:" in t and "Guardar y volver a revisar" in t
    fake.respuestas = [{"veredicto": "concluido", "motivo": "La aclaración explica las citas en el CRM."}]
    manuel.post(f"/compromisos/{c.id}/aclarar", data={"texto": "Envié la campaña; las 2 citas están en el CRM de HubSpot del 12 y 13"})
    db.expire_all()
    c = db.get(Compromiso, c.id)
    assert c.cierre_veredicto == "concluido" and c.cierre_detalle["comentarios_previos"][0]["texto"] == "Envié la campaña"
    assert "HubSpot" in str(fake.prompts[-1])


def test_cliente_de_la_sesion(admin, db):
    jm = _u(db, "jmartinez@onecore.mx")
    eg = _u(db, "egarza@onecore.mx")
    a = AgendaCliente(mes=date.today().strftime("%Y-%m"), tipo="operativa", area_id=jm.area_id, responsable_id=jm.id,
                      fecha=date.today(), creado_por=jm.id, invitados=[{"nombre": eg.nombre, "email": eg.email, "lado": "onecore"}],
                      historial=[])
    db.add(a)
    db.commit()
    assert cl.ve_cliente(eg, a) and cl.ve_cliente(_u(db, "gtrevino@onecore.mx"), a)  # el nombre lo ve todo el equipo
    # al subir la transcripción sin cliente, se pide
    r = admin.post(f"/clientes/agenda/{a.id}/transcripcion", data={"consentimiento": "true"},
                   files={"archivo": ("t.txt", b"hola " * 50, "text/plain")})
    db.refresh(a)
    assert a.sesion_id is None
    admin.post(f"/clientes/agenda/{a.id}/cliente", data={"cliente_nombre": "Manufacturas del Norte"})
    db.refresh(a)
    assert a.cliente_nombre == "Manufacturas del Norte" and "sin cliente" in a.historial[-1]["texto"]
    # un usuario sin permiso de confidencial ni relación con la sesión ve el nombre, no «Cliente · Sesión #…»
    erick = login(TestClient(app), "egarza@onecore.mx")
    lista = erick.get(f"/clientes/agenda?mes={a.mes}").text
    assert "Manufacturas del Norte" in lista and f"Sesión #{a.id:04d}</b>" not in lista


def test_series_quincenal_y_trimestral():
    ini = date(2026, 12, 7)  # lunes
    q = cl.fechas_serie(ini, "quincenal", date(2027, 2, 28), dia_semana=0)
    assert [f for f, _ in q][:3] == [date(2026, 12, 7), date(2026, 12, 21), date(2027, 1, 4)]
    assert all(f.weekday() == 0 or nota for f, nota in q)  # lunes, salvo los recorridos por festivo (con nota)
    assert any("festivo" in nota for _, nota in q)  # 1 feb 2027 (Constitución) se recorre
    tr = cl.fechas_serie(date(2026, 12, 1), "trimestral", date(2027, 11, 30), modo="nesimo", dia_semana=0, nesimo=1)
    assert [f.month for f, _ in tr] == [12, 3, 6, 9]  # primer lunes de dic, mar, jun, sep
    tr2 = cl.fechas_serie(date(2026, 12, 1), "trimestral", date(2027, 11, 30), modo="dia", dia_mes=27)
    assert [f.month for f, _ in tr2] == [12, 3, 6, 9] and all(not cl.inhabil(f) for f, _ in tr2)
    assert "cada 2 semanas" in cl.describir_serie("quincenal", dia_semana=0)
    assert "cada 3 meses" in cl.describir_serie("trimestral", "dia", dia_mes=27)

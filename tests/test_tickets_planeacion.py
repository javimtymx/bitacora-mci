"""Jefe directo, copias, etapa Planeado, costos, pagos, lista de tickets y subidas directas a S3."""
import io
from datetime import date, timedelta

from sqlalchemy import select

from app.models import Ticket, TicketPago, Usuario
from app.services import almacen, subidas
from app.services import tickets as tk
from app.services import tickets_correo


class FakeS3:
    def __init__(self):
        self.objetos = {}

    def put_object(self, Bucket, Key, Body, ContentType, ServerSideEncryption):
        self.objetos[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objetos[Key])}

    def delete_object(self, Bucket, Key):
        self.objetos.pop(Key, None)

    def head_object(self, Bucket, Key):
        if Key not in self.objetos:
            raise KeyError(Key)
        return {"ContentLength": len(self.objetos[Key])}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://s3.test/{op}/{Params['Key']}"


def _u(db, email):
    return db.scalar(select(Usuario).where(Usuario.email == email))


def _ticket(db, autor, **kw):
    campos = {"descripcion": "Al timbrar la factura en CIVA sale error de sello en todas las facturas del día",
              "producto": "CIVA", "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: error de sello",
              "referencia": "ABC010101AAA", "pasos": "1. Facturar 2. Timbrar"}
    campos.update(kw)
    t = tk.crear(db, autor, campos, [], validacion="completo", faltantes=[], conversacion=[], prompt_version=None)
    db.commit()
    return t


def _editar(admin, x, **kw):
    data = {"nombre": x.nombre, "rol": x.rol, "menus_form": "1", "recibe_correos": "on", **kw}
    return admin.post(f"/admin/usuarios/{x.id}", data=data, follow_redirects=False)


def test_jefe_y_costo_por_hora_en_usuarios(admin, db):
    jm, manuel, mario = (_u(db, e) for e in ("jmartinez@onecore.mx", "mcoronado@onecore.mx", "mpineda@onecore.mx"))
    _editar(admin, manuel, jefe_id=str(mario.id))
    _editar(admin, jm, costo_hora="$1,250.50")
    db.expire_all()
    assert manuel.jefe_id == mario.id and float(jm.costo_hora) == 1250.5
    # no se permiten ciclos: mario no puede tener de jefe a quien le reporta
    _editar(admin, mario, jefe_id=str(manuel.id))
    db.expire_all()
    assert mario.jefe_id is None
    html = admin.get("/admin/usuarios").text
    assert "Jefe directo" in html and "1,250.50" in html


def test_planear_mover_cerrar_y_costos(admin, manuel, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    jm.costo_hora = 500
    db.commit()
    t = _ticket(db, m)
    t.estado = "pendiente"
    db.commit()
    # sin plan no se puede pasar a En curso; el tablero recibe JSON con la pista para abrir el modal
    r = admin.post(f"/tickets/{t.id}/mover", data={"estado": "en_curso"}, headers={"Accept": "application/json"})
    assert r.status_code == 400 and r.json()["planear"]
    # planear exige horas, fecha e ingeniero
    entrega = date.today() + timedelta(days=5)
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "6", "fecha_entrega": entrega.isoformat()})
    db.refresh(t)
    assert t.estado == "pendiente"
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "6", "fecha_entrega": entrega.isoformat(), "asignado_id": jm.id})
    db.refresh(t)
    assert t.estado == "planeado" and float(t.horas_estimadas) == 6 and t.fecha_entrega == entrega and t.asignado_id == jm.id
    r = admin.post(f"/tickets/{t.id}/mover", data={"estado": "en_curso"}, headers={"Accept": "application/json"})
    assert r.json() == {"ok": True, "estado": "en_curso"}
    # el colaborador no planea
    assert manuel.post(f"/tickets/{t.id}/planear", data={"horas": "1"}).status_code == 403
    # el tablero tiene la columna Planeado y arrastrar y soltar
    html = admin.get("/tickets-tablero").text
    assert 'data-estado="planeado"' in html and "dragstart" in html
    # cerrar: sin horas reales toma las planeadas; la tarifa se guarda al cerrar
    admin.post(f"/tickets/{t.id}/cerrar", data={"tipo_cierre": "corregido", "causa": "El certificado estaba vencido",
                                                "solucion": "Se renovó el certificado de sello digital del emisor"})
    db.refresh(t)
    assert t.estado == "cerrado" and float(t.horas_reales) == 6 and float(t.costo_hora) == 500 and t.costo == 3000
    jm.costo_hora = 900  # cambiar la tarifa no altera lo ya cerrado
    db.commit()
    db.refresh(t)
    assert t.costo == 3000
    # el costo solo lo ve Dirección
    assert "3,000.00" in admin.get(f"/tickets/{t.id}").text
    assert "3,000.00" not in manuel.get(f"/tickets/{t.id}").text


def test_correos_con_jefe_copias_y_cambio_de_etapa(db):
    jm, m, mario = (_u(db, e) for e in ("jmartinez@onecore.mx", "mcoronado@onecore.mx", "mpineda@onecore.mx"))
    m.jefe_id = mario.id
    db.commit()
    t = _ticket(db, m)
    try:
        tk.limpiar_cc("mal-correo")
        assert False
    except tk.TicketInvalido:
        pass
    t.cc = tk.limpiar_cc("Cliente@Empresa.com; otro@onecore.mx, cliente@empresa.com")
    db.commit()
    assert t.cc == ["cliente@empresa.com", "otro@onecore.mx"]
    nuevo = tickets_correo.avisos(db, "nuevo", t, m)
    assert nuevo[0][0] == ["mcoronado@onecore.mx", "mpineda@onecore.mx", "cliente@empresa.com", "otro@onecore.mx"]
    tk.planear(db, jm, t, horas="4", fecha_entrega=date.today() + timedelta(days=2), asignado=_u(db, "mpineda@onecore.mx"))
    db.commit()
    av = tickets_correo.avisos(db, "etapa", t, jm)
    asuntos = {a[1]: a[0] for a in av}
    assert any("planeado para ti" in s for s in asuntos), asuntos
    etapa = [c for s, c in asuntos.items() if "etapa Planeado" in s][0]
    assert "mcoronado@onecore.mx" in etapa and "mpineda@onecore.mx" in etapa and "cliente@empresa.com" in etapa
    assert "jmartinez@onecore.mx" not in etapa  # no se avisa a quien hizo el cambio


def test_lista_pestanas_alcance_y_buscador(manuel, mario, db):
    m, mp, jm = _u(db, "mcoronado@onecore.mx"), _u(db, "mpineda@onecore.mx"), _u(db, "jmartinez@onecore.mx")
    m.jefe_id = mp.id
    db.commit()
    t1 = _ticket(db, m)
    t2 = _ticket(db, jm, titulo="Reporte de cartera lento", descripcion="El reporte de cartera vencida tarda más de cinco minutos",
                 producto="Cartera", tipo="mejora")
    tk.planear(db, jm, t2, horas="3", fecha_entrega=date.today(), asignado=jm)
    t2.fecha_entrega = date.today() - timedelta(days=1)  # se le pasó la fecha
    db.commit()
    html = manuel.get("/tickets").text
    assert f"<b>{t1.folio}</b>" in html and f"<b>{t2.folio}</b>" not in html
    html = manuel.get("/tickets?ver=empresa&tab=programados").text
    assert t2.folio in html and "Vencida" in html
    html = mario.get("/tickets?ver=equipo&tab=pendientes").text  # el jefe ve lo de su equipo
    assert t1.folio in html and "Mi equipo" in html
    assert t2.folio in manuel.get(f"/tickets?ver=empresa&tab=todos&q={t2.folio}").text
    assert t2.folio in manuel.get(f"/tickets?ver=empresa&tab=todos&q={t2.id}").text
    html = manuel.get("/tickets?ver=empresa&tab=todos&q=cartera vencida").text
    assert f"<b>{t2.folio}</b>" in html and f"<b>{t1.folio}</b>" not in html


def test_informe_y_pagos_con_comprobante(admin, manuel, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    jm.costo_hora = 400
    db.commit()
    ts = []
    for i, h in enumerate(("5", "2.5")):
        t = _ticket(db, m, titulo=f"Ticket {i}")
        tk.planear(db, jm, t, horas=h, fecha_entrega=date.today(), asignado=jm)
        tk.cerrar(db, jm, t, tipo_cierre="corregido", causa="Causa larga del problema",
                  solucion="Solución aplicada en la validación", horas_reales=h)
        ts.append(t)
    abierto = _ticket(db, m, titulo="Abierto")
    tk.planear(db, jm, abierto, horas="10", fecha_entrega=date.today() + timedelta(days=3), asignado=jm)
    db.commit()
    # solo Dirección (o quien tenga el permiso) ve el informe y los pagos
    assert manuel.get("/tickets-informe").status_code == 403
    assert manuel.get("/tickets-pagos").status_code == 403
    html = admin.get("/tickets-informe").text
    assert "3,000.00" in html and "4,000.00" in html  # ejecutado 7.5 h × 400 y pronóstico 10 h × 400
    assert "CIVA" in html and "Manuel Coronado" in html
    csv = admin.get("/tickets-informe?formato=csv").text
    assert ts[0].folio in csv and "Costo" in csv
    rango = admin.get(f"/tickets-informe?desde={date.today() - timedelta(days=40)}&hasta={date.today() - timedelta(days=35)}").text
    assert ts[0].folio not in rango
    # pago sin comprobante: no se registra
    admin.post("/tickets-pagos", data={"ingeniero_id": jm.id, "tickets": [ts[0].id], "monto": "2000"})
    assert db.scalar(select(TicketPago)) is None
    # pago con comprobante (subida tradicional, sin S3)
    r = admin.post("/tickets-pagos", data={"ingeniero_id": jm.id, "tickets": [ts[0].id], "monto": "2,000",
                                           "fecha": date.today().isoformat(), "nota": "SPEI"},
                   files={"comprobante": ("spei.pdf", b"%PDF-1.4 comprobante", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303
    p = db.scalar(select(TicketPago))
    db.refresh(ts[0])
    assert ts[0].pago_id == p.id and float(p.monto) == 2000 and p.comprobante_nombre == "spei.pdf"
    assert admin.get(f"/tickets-pagos/{p.id}/comprobante").content == b"%PDF-1.4 comprobante"
    # no se paga dos veces ni se reabre un ticket pagado
    admin.post("/tickets-pagos", data={"ingeniero_id": jm.id, "tickets": [ts[0].id], "monto": "1"},
               files={"comprobante": ("x.pdf", b"%PDF", "application/pdf")})
    assert len(db.scalars(select(TicketPago)).all()) == 1
    admin.post(f"/tickets/{ts[0].id}/reabrir", data={"motivo": "Volvió a fallar el sello"})
    db.refresh(ts[0])
    assert ts[0].estado == "cerrado"
    html = admin.get("/tickets-pagos").text
    assert ts[1].folio in html and "spei.pdf" in html
    # el ingeniero ve lo pagado y lo pendiente, y baja su comprobante
    html = admin.get("/tickets-tablero/mis-pagos").text
    assert "2,000.00" in html and ts[1].folio in html
    assert admin.get(f"/tickets-tablero/mis-pagos/{p.id}/comprobante").status_code == 200


def test_subida_directa_a_s3(admin, manuel, db):
    s3 = FakeS3()
    almacen.set_s3(s3)
    try:
        r = manuel.post("/api/subidas/firmar", json={"destino": "ticket", "nombre": "video.mp4", "tamano": 900 * 1024 ** 2})
        f = r.json()
        assert r.status_code == 200 and f["grande"] and f["url"].startswith("https://s3.test/put_object/")
        assert manuel.post("/api/subidas/firmar", json={"destino": "ticket", "nombre": "x.exe", "tamano": 10}).status_code == 400
        assert manuel.post("/api/subidas/firmar",
                           json={"destino": "ticket", "nombre": "x.zip", "tamano": 6 * 1024 ** 3}).status_code == 400
        llave = f["url"].rsplit("/put_object/", 1)[1]
        # si el navegador no terminó de subir, el servidor lo rechaza
        r = manuel.post("/api/tickets/nuevo", data={"descripcion": "El video muestra el error al timbrar en CIVA paso a paso",
                                                    "producto": "CIVA", "tipo": "error", "impacto": "bloquea",
                                                    "referencia": "ABC010101AAA", "pasos": "1. x", "validacion": "completo",
                                                    "subidas": [f["token"]]})
        assert r.status_code == 400
        s3.objetos[llave] = b"x" * 1234
        r = manuel.post("/api/tickets/nuevo", data={"descripcion": "El video muestra el error al timbrar en CIVA paso a paso",
                                                    "producto": "CIVA", "tipo": "error", "impacto": "bloquea",
                                                    "referencia": "ABC010101AAA", "pasos": "1. x", "validacion": "completo",
                                                    "subidas": [f["token"]]})
        assert r.status_code == 200, r.text
        t = db.scalar(select(Ticket).order_by(Ticket.id.desc()))
        assert t.anexos[0].nombre == "video.mp4" and t.anexos[0].bytes == 1234 and t.anexos[0].ruta == "s3:" + llave
        # un token es de quien lo pidió
        try:
            subidas.recibir(f["token"], 999, "ticket")
            assert False
        except subidas.SubidaInvalida:
            pass
    finally:
        almacen.set_s3(None)
    # sin S3 el formulario usa la subida tradicional
    assert manuel.post("/api/subidas/firmar", json={"destino": "ticket", "nombre": "a.png", "tamano": 10}).status_code == 409


def test_historia_de_estimacion_y_desviacion(admin, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    jm.costo_hora = 500
    db.commit()
    t = _ticket(db, m)
    hoy = date.today()
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "6", "fecha_entrega": hoy.isoformat(), "asignado_id": jm.id})
    db.refresh(t)
    assert float(t.horas_originales) == 6 and not t.horas_modificadas and len(t.estimaciones) == 1
    # cambiar las horas sin motivo no se permite
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "10", "fecha_entrega": hoy.isoformat(), "asignado_id": jm.id})
    db.refresh(t)
    assert float(t.horas_estimadas) == 6
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "10", "fecha_entrega": hoy.isoformat(), "asignado_id": jm.id,
                                                 "motivo": "También afecta el reporte de diferencias"})
    # cambiar solo la fecha no exige motivo y crea versión
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "10", "fecha_entrega": (hoy + timedelta(days=2)).isoformat(),
                                                 "asignado_id": jm.id})
    db.refresh(t)
    assert [float(e.horas) for e in t.estimaciones] == [6, 10, 10] and t.estimaciones[1].motivo.startswith("También")
    assert t.horas_modificadas and float(t.horas_originales) == 6 and t.desviacion_plan == 4
    html = admin.get(f"/tickets/{t.id}").text
    assert "Horas modificadas" in html and "Historia de la estimación" in html and "v3" in html
    assert "Horas modificadas" in admin.get("/tickets-tablero").text
    tk.cerrar(db, jm, t, tipo_cierre="corregido", causa="Causa larga del problema", solucion="Solución aplicada en la validación",
              horas_reales="12")
    db.commit()
    assert t.desviacion_real == 6 and t.desviacion_pct == 100.0
    html = admin.get("/tickets-informe").text
    assert "Desviación contra la estimación original" in html and "+6.0 h" in html and "3,000.00" in html  # 6 h × 500
    csv = admin.get("/tickets-informe?formato=csv").text
    assert "Horas originales" in csv and ",sí," in csv


def test_catalogo_de_areas_de_usuario(admin, manuel, db):
    m = _u(db, "mcoronado@onecore.mx")
    html = admin.get("/admin/areas-usuario").text
    assert "Áreas de usuarios" in html
    ops = tk.opciones(db, "area", True)
    assert ops  # arranca con las áreas de la empresa
    filas = {f"id_{i}": o.id for i, o in enumerate(ops)}
    data = {**filas, **{f"etiqueta_{i}": o.etiqueta for i, o in enumerate(ops)},
            **{f"activo_{i}": "1" for i in range(len(ops))}, f"etiqueta_{len(ops)}": "Operaciones aduanales",
            f"orden_{len(ops)}": "50"}
    admin.post("/admin/areas-usuario", data=data)
    db.expire_all()
    areas = tk.areas_usuario(db)
    clave = [k for k, v in areas.items() if v == "Operaciones aduanales"][0]
    _editar(admin, m, area_ticket=clave)
    db.expire_all()
    assert m.area_ticket == clave
    t = _ticket(db, m)
    assert t.area == clave
    assert "Operaciones aduanales" in admin.get(f"/tickets/{t.id}").text
    # quitar el área: deja de ofrecerse pero se conserva en el ticket y en el usuario
    o = [x for x in tk.opciones(db, "area", True) if x.clave == clave][0]
    data2 = {f"id_{i}": x.id for i, x in enumerate(tk.opciones(db, "area", True))}
    data2.update({f"etiqueta_{i}": x.etiqueta for i, x in enumerate(tk.opciones(db, "area", True))})
    data2.update({f"activo_{i}": "1" for i, x in enumerate(tk.opciones(db, "area", True)) if x.id != o.id})
    admin.post("/admin/areas-usuario", data=data2)
    db.expire_all()
    assert clave not in tk.areas_usuario(db) and clave in tk.areas_usuario(db, True)
    db.refresh(t)
    assert t.area == clave and "Operaciones aduanales" in admin.get(f"/tickets/{t.id}").text
    assert manuel.get("/admin/areas-usuario").status_code in (403, 303, 307)


def test_apagar_aviso_de_triage(admin, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    t = _ticket(db, m)
    destinos = lambda: [c for cs, asunto, *_ in tickets_correo.avisos(db, "nuevo", t, m) if "por clasificar" in asunto for c in cs]
    assert "jmartinez@onecore.mx" in destinos()
    _editar(admin, jm, avisos_triage="")  # se desmarca la casilla
    db.expire_all()
    assert jm.avisos_triage is False and jm.recibe_correos
    assert "jmartinez@onecore.mx" not in destinos()


def test_catalogo_de_productos_abc(admin, manuel, db):
    m = _u(db, "mcoronado@onecore.mx")
    t = _ticket(db, m, producto="CIVA")
    html = admin.get("/tickets-config").text
    assert "Productos" in html and "Nuevo producto" in html
    ops = tk.opciones(db, "producto", True)
    assert "CIVA" in [o.etiqueta for o in ops]

    def guardar(cambios=None, nuevo=None, baja=()):
        ops = tk.opciones(db, "producto", True)
        data = {}
        for i, o in enumerate(ops):
            data[f"id_{i}"] = o.id
            data[f"etiqueta_{i}"] = (cambios or {}).get(o.etiqueta, o.etiqueta)
            data[f"orden_{i}"] = o.orden
            if o.etiqueta not in baja:
                data[f"activo_{i}"] = "1"
        if nuevo:
            data[f"etiqueta_{len(ops)}"], data[f"orden_{len(ops)}"] = nuevo, "90"
        r = admin.post("/tickets-config/opciones/producto", data=data, follow_redirects=False)
        db.expire_all()
        return r

    # alta
    guardar(nuevo="Procesos Productivos")
    assert "Procesos Productivos" in tk.productos(db)
    assert "Procesos Productivos" in manuel.get("/tickets/nuevo").text
    # no se permiten nombres repetidos
    guardar(nuevo="civa")
    assert len([p for p in tk.productos(db, True) if p.lower() == "civa"]) == 1
    # cambio de nombre: se actualizan sus tickets
    guardar(cambios={"CIVA": "CIVA Certificación"})
    db.refresh(t)
    assert t.producto == "CIVA Certificación" and "CIVA Certificación" in tk.catalogo(db)
    # baja: deja de ofrecerse pero el ticket lo conserva
    guardar(baja=("CIVA Certificación",))
    assert "CIVA Certificación" not in tk.productos(db) and "CIVA Certificación" not in tk.catalogo(db)
    db.refresh(t)
    assert t.producto == "CIVA Certificación"
    # al levantar: producto fuera del catálogo se rechaza; sin importar mayúsculas se acepta con el nombre del catálogo
    base = {"descripcion": "Al timbrar la factura sale error de sello en todas las facturas del día", "tipo": "error",
            "impacto": "bloquea", "referencia": "ABC010101AAA", "pasos": "1. x", "validacion": "completo"}
    r = manuel.post("/api/tickets/nuevo", data={**base, "producto": "CIVA Certificación"})
    assert r.status_code == 400 and "lista" in r.json()["mensaje"]
    r = manuel.post("/api/tickets/nuevo", data={**base, "producto": "procesos productivos"})
    assert r.status_code == 200
    db.expire_all()
    assert db.scalar(select(Ticket).order_by(Ticket.id.desc())).producto == "Procesos Productivos"

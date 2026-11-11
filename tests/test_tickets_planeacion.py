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


def test_rendicion_de_cuentas_pdf(admin, db):
    from app.models import Area, Compromiso, CompromisoNota, Mci, Predictiva, Remediacion
    from app.services.util import hoy, semana_iso
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    m = db.scalar(select(Mci).where(Mci.area_id == a.id))
    p = db.scalar(select(Predictiva).where(Predictiva.mci_id == m.id))
    jm = _u(db, "jmartinez@onecore.mx")
    sem = semana_iso(hoy())
    largo = "Enviar propuesta formal con el comparativo de SAFE contra su proceso actual de validación de pedimentos " * 3
    ok = Compromiso(semana=sem, area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                    texto="Visitar a tres clientes clave", fecha_limite=hoy(), estado="cumplido", avance=100)
    mal = Compromiso(semana=sem, area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                     texto=largo, fecha_limite=hoy(), estado="no_cumplido")
    suelto = Compromiso(semana=sem, area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=None,
                        texto="Documentar objeciones", fecha_limite=hoy() + timedelta(days=3), estado="abierto")
    db.add_all([ok, mal, suelto])
    db.flush()
    db.add(Remediacion(compromiso_id=mal.id, causa="El cliente pospuso la reunión", accion="Reagendar y enviar por correo",
                       nueva_fecha=hoy() + timedelta(days=4), creado_por=jm.id))
    db.add(CompromisoNota(compromiso_id=ok.id, usuario_id=jm.id, avance=100, texto="Dos de tres cerraron demo"))
    db.commit()
    html = admin.get(f"/semanal?semana={sem}").text
    assert largo.strip() in html  # el texto va completo
    assert "El cliente pospuso la reunión" in html and "Reagendar y enviar por correo" in html
    assert "Dos de tres cerraron demo" in html and "sin medida predictiva" in html and p.nombre in html and "CMP-" in html
    assert html.index(largo.strip()) < html.index("Visitar a tres clientes clave")  # no cumplido antes que cumplido
    assert "Cumplimiento" in html
    assert admin.get("/semanal?tipo=mes").status_code == 200
    r = admin.get(f"/semanal?tipo=rango&desde={hoy() - timedelta(days=10)}&hasta={hoy()}&area={a.clave}")
    assert r.status_code == 200 and "Visitar a tres clientes clave" in r.text


def test_botones_en_curso_y_revision_planean_primero(admin, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    t = _ticket(db, m)
    t.estado = "pendiente"
    db.commit()
    html = admin.get(f"/tickets/{t.id}").text
    assert 'data-despues="en_curso"' in html and 'data-despues="revision"' in html
    # la regla no cambia: sin plan no se mueve
    admin.post(f"/tickets/{t.id}/mover", data={"estado": "en_curso"})
    db.refresh(t)
    assert t.estado == "pendiente"
    # desde el botón «En curso»: se captura el plan y queda en curso
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "4", "fecha_entrega": date.today().isoformat(),
                                                 "asignado_id": jm.id, "despues": "en_curso"})
    db.refresh(t)
    assert t.estado == "en_curso" and float(t.horas_estimadas) == 4
    # con plan, los botones vuelven a ser movimientos directos
    html = admin.get(f"/tickets/{t.id}").text
    assert 'name="estado" value="revision"' in html
    admin.post(f"/tickets/{t.id}/mover", data={"estado": "revision"})
    db.refresh(t)
    assert t.estado == "revision"


def test_rendicion_todo_el_historial_y_dias_vencidos(admin, db):
    from app.models import Area, Compromiso, Mci, Predictiva
    from app.services.util import hoy, semana_iso
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.area_id == a.id))
    jm = _u(db, "jmartinez@onecore.mx")
    viejo = Compromiso(semana="2025-W10", area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                       texto="Compromiso muy demorado de hace meses", fecha_limite=hoy() - timedelta(days=12), estado="abierto")
    cerrado = Compromiso(semana=semana_iso(hoy()), area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                         texto="Compromiso ya cerrado", fecha_limite=hoy(), estado="cumplido", avance=100)
    db.add_all([viejo, cerrado])
    db.commit()
    html = admin.get("/semanal?tipo=todo&mostrar=abiertos").text
    assert "Compromiso muy demorado de hace meses" in html and "Vencido hace 12 días" in html and "rp-venc" in html
    assert "Compromiso ya cerrado" not in html and "el más atrasado 12 días" in html
    html = admin.get("/semanal?tipo=todo&mostrar=cerrados").text
    assert "Compromiso ya cerrado" in html and "Compromiso muy demorado" not in html
    html = admin.get(f"/semanal?semana={semana_iso(hoy())}").text
    assert "Compromiso muy demorado" not in html  # fuera de la semana elegida


def test_catalogo_como_se_resolvio(admin, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    assert "¿Cómo se resolvió?" in admin.get("/tickets-config").text
    ops = tk.opciones(db, "cierre", True)
    assert "corregido" in [o.clave for o in ops]
    data = {}
    for i, o in enumerate(ops):
        data.update({f"id_{i}": o.id, f"etiqueta_{i}": o.etiqueta, f"orden_{i}": o.orden})
        if o.clave != "duplicado":
            data[f"activo_{i}"] = "1"
    data.update({f"etiqueta_{len(ops)}": "Escalado al proveedor", f"orden_{len(ops)}": "90"})
    admin.post("/tickets-config/opciones/cierre", data=data)
    db.expire_all()
    act = tk.cierres(db)
    assert "duplicado" not in act and "Escalado al proveedor" in act.values()
    clave = [k for k, v in act.items() if v == "Escalado al proveedor"][0]
    t = _ticket(db, m)
    html = admin.get(f"/tickets/{t.id}").text
    assert "Escalado al proveedor" in html and 'value="duplicado"' not in html
    try:
        tk.cerrar(db, jm, t, tipo_cierre="duplicado", causa="Causa larga del problema", solucion="Solución aplicada en la validación",
                  horas_reales="1")
        assert False
    except tk.TicketInvalido:
        db.rollback()
    tk.cerrar(db, jm, t, tipo_cierre=clave, causa="Causa larga del problema", solucion="Solución aplicada en la validación",
              horas_reales="1")
    db.commit()
    assert "Escalado al proveedor" in admin.get(f"/tickets/{t.id}").text


def test_pago_agrupado_fija_la_tarifa(admin, db):
    jm, m = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    jm.costo_hora = None
    db.commit()
    ts = []
    for h in ("2", "3"):
        t = _ticket(db, m)
        tk.planear(db, jm, t, horas=h, fecha_entrega=date.today(), asignado=jm)
        tk.cerrar(db, jm, t, tipo_cierre="corregido", causa="Causa larga del problema",
                  solucion="Solución aplicada en la validación", horas_reales=h)
        ts.append(t)
    db.commit()
    assert all(t.costo_hora is None for t in ts)  # se cerraron sin tarifa
    pdf = ("c.pdf", b"%PDF-1.4", "application/pdf")
    admin.post("/tickets-pagos", data={"ingeniero_id": jm.id, "tickets": [t.id for t in ts], "monto": "1"},
               files={"comprobante": pdf})
    assert db.scalar(select(TicketPago)) is None  # sin tarifa no se calcula el pago
    jm.costo_hora = 500
    db.commit()
    html = admin.get(f"/tickets-pagos?ingeniero={jm.id}").text
    assert "2,500.00" in html and "vigente, se fija al pagar" in html  # 5 h × 500
    r = admin.post("/tickets-pagos", data={"ingeniero_id": jm.id, "tickets": [t.id for t in ts], "monto": "2500",
                                           "fecha": date.today().isoformat()}, files={"comprobante": pdf},
                   follow_redirects=False)
    assert r.status_code == 303
    p = db.scalar(select(TicketPago))
    db.expire_all()
    assert len(p.tickets) == 2 and float(p.monto) == 2500
    assert all(float(t.costo_hora) == 500 for t in p.tickets)
    jm.costo_hora = 900  # cambia el catálogo: lo pagado no se recalcula
    db.commit()
    db.expire_all()
    assert sum(t.costo for t in p.tickets) == 2500


def test_rendicion_muestra_evidencias(admin, db):
    from app.models import Area, Compromiso, Mci, Predictiva
    from app.services import evidencias
    from app.services.util import hoy, semana_iso
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.area_id == a.id))
    jm = _u(db, "jmartinez@onecore.mx")
    c = Compromiso(semana=semana_iso(hoy()), area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                   texto="Compromiso con evidencia", fecha_limite=hoy(), estado="abierto", avance=50)
    db.add(c)
    db.flush()
    png = b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 64
    ev = evidencias.guardar(db, c, "foto.png", png, jm)
    db.commit()
    html = admin.get("/semanal?tipo=todo").text
    assert "Ver evidencias (1)" in html and "foto.png" in html and 'id="rp-visor"' in html
    r = admin.get(f"/evidencias/{ev.id}?ver=1")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    assert admin.get(f"/evidencias/{ev.id}").headers["content-disposition"].startswith("attachment")


def test_tablero_de_tareas_subtareas_y_alta_rapida(admin, db, fake):
    from app.models import Area, Compromiso, CompromisoSubtarea, Mci, Predictiva
    from app.services.util import hoy, semana_iso
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.area_id == a.id))
    jm = _u(db, "jmartinez@onecore.mx")
    c = Compromiso(semana=semana_iso(hoy()), area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                   texto="Visitar a tres clientes clave del corredor Monterrey", fecha_limite=hoy() + timedelta(days=2))
    db.add(c)
    db.commit()
    html = admin.get(f"/?area={a.clave}").text
    assert 'data-vista="tablero"' in html and "Tablero de tareas" in html and f'data-comp="{c.id}"' in html
    assert "+ Agregar compromiso" in html
    # subtareas
    r = admin.post(f"/compromisos/{c.id}/subtareas", data={"texto": "Confirmar agenda con los clientes"})
    assert r.json()["ok"]
    sid = r.json()["subtarea"]["id"]
    assert admin.post(f"/subtareas/{sid}/marcar", data={"hecha": "true"}).json()["subtarea"]["hecha"] is True
    db.expire_all()
    assert db.get(CompromisoSubtarea, sid).hecha and c.avance == 0  # la subtarea no cambia el avance
    assert admin.post(f"/subtareas/{sid}/quitar").status_code == 400  # ya hecha no se quita
    assert "Confirmar agenda con los clientes" in admin.get(f"/?area={a.clave}").text
    # alta rápida: mismas reglas que el formulario (con IA activa exige la revisión; si es débil, se rechaza)
    base = {"area_id": a.id, "predictiva_id": p.id, "responsable_id": jm.id, "fecha_limite": (hoy() + timedelta(days=3)).isoformat()}
    txt = "Llamar a 3 clientes con riesgo alto y registrar su respuesta"
    r = admin.post("/compromisos", data={**base, "texto": txt}, headers={"Accept": "application/json"})
    assert r.status_code == 400 and "revisión" in r.json()["mensaje"]
    fake.respuestas = [{"veredicto": "ok", "observaciones": []}]
    rev = admin.post("/api/ia/compromiso", data={**base, "texto": txt}).json()
    r = admin.post("/compromisos", data={**base, "texto": txt, "revision_id": rev["revision_id"], "decision": "ok"},
                   headers={"Accept": "application/json"})
    assert r.json()["ok"]
    r = admin.post("/compromisos", data={**base, "texto": "seguimiento"}, headers={"Accept": "application/json"})
    assert r.status_code == 400


def test_visor_en_tablero_con_texto_completo_y_notas(admin, db):
    from app.models import Area, Compromiso, CompromisoNota, Mci, Predictiva
    from app.services import evidencias
    from app.services.util import hoy, semana_iso
    a = db.scalar(select(Area).join(Mci, Mci.area_id == Area.id))
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.area_id == a.id))
    jm = _u(db, "jmartinez@onecore.mx")
    largo = "Documentar los acuerdos de cada cuenta en riesgo con su minuta y siguiente paso " * 4
    c = Compromiso(semana=semana_iso(hoy()), area_id=a.id, responsable_id=jm.id, autor_id=jm.id, predictiva_id=p.id,
                   texto=largo.strip(), fecha_limite=hoy(), avance=30)
    db.add(c)
    db.flush()
    evidencias.guardar(db, c, "minuta.png", b"\\x89PNG\\r\\n\\x1a\\n" + b"0" * 64, jm)
    db.add(CompromisoNota(compromiso_id=c.id, usuario_id=jm.id, avance=30, texto="Dos de cinco clientes ya respondieron"))
    db.commit()
    for url in (f"/?area={a.clave}", "/semanal?tipo=todo"):
        html = admin.get(url).text
        assert 'data-visor' in html and 'id="rp-visor"' in html and "visor_evidencias.js" in html
        assert "Dos de cinco clientes ya respondieron" in html and largo.strip()[-60:] in html

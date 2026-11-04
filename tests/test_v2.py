"""Tablero, kárdex, diagnóstico IA, voz del cliente y círculo de innovación."""
from datetime import timedelta

from sqlalchemy import select

from app.models import (CirculoInnovacion, Compromiso, DiagnosticoSemanal, Estrategia, Hallazgo, Idea, Iniciativa, Mci,
                        Medicion, Objecion, Predictiva, PromptArea, RecomendacionIA, Remediacion, SesionCliente, Usuario)
from app.services import indicadores, parametros, vozcliente
from app.services.util import hoy, semana_iso

TRANSCRIPCION = ("Gracias por recibirnos. Nos interesa saber cómo les ha ido con la plataforma este trimestre. "
                 "Mira, en general bien, pero cuando VUCEM se cae SAFE no nos deja validar y perdemos el día de despacho completo. "
                 "Eso nos pasó dos veces en octubre y la dirección ya lo notó. También nos serviría que la manifestación de valor "
                 "tome los datos del COVE sin recapturar, porque hoy lo hacemos a mano y tardamos mucho. "
                 "El soporte responde bien, aunque a veces el primer contacto tarda más de un día. "
                 "Antes de renovar en diciembre nos van a pedir justificar el costo frente a otra opción más barata. "
                 "Quedamos en enviarles el plan de contingencia de SAFE el viernes y ustedes nos comparten los pedimentos que fallaron. ") * 2


def _manuel(db):
    return db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))


def _mci(db, clave="ventas-base"):
    return db.scalar(select(Mci).where(Mci.clave == clave))


def test_paginas_nuevas(admin, db):
    m = _mci(db)
    for url in ["/", "/?area=ventas", "/mi-semana", f"/kardex/{m.id}", "/indicadores/captura", "/diagnostico",
                "/diagnostico?area=retencion", "/admin/prompts", "/voz-cliente", "/voz-cliente?tipo=objeciones",
                "/circulo", "/sesiones/nueva", "/admin/catalogo"]:
        r = admin.get(url)
        assert r.status_code == 200, (url, r.text[:400])
    assert admin.get("/kardex?area=ventas", follow_redirects=False).headers["location"] == f"/kardex/{m.id}"


def test_captura_y_serie_acumulada(admin, db):
    m = _mci(db)
    v = m.versiones[-1]
    v.tipo_meta, v.linea_base, v.fecha_meta = "acumulado", 0, v.vigente_desde + timedelta(days=364)
    db.commit()
    sems = indicadores.semanas(3)
    for s, val in zip(sems, (10, 12, 11)):
        r = admin.post("/indicadores/captura", data={"semana": s, f"valor_{m.id}": str(val)}, follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert len(list(db.scalars(select(Medicion)))) == 3
    sr = indicadores.serie_mci(db, db.get(Mci, m.id), 3)
    assert [p.valor for p in sr.puntos] == [10, 12, 11]
    assert all(p.objetivo is not None and 0 < p.objetivo < 35 for p in sr.puntos)
    racha, n = sr.racha
    assert racha in ("ganando", "perdiendo")
    assert "Kárdex" in admin.get(f"/kardex/{m.id}").text
    # recaptura de la misma semana actualiza, no duplica
    admin.post("/indicadores/captura", data={"semana": sems[-1], f"valor_{m.id}": "15"})
    db.expire_all()
    assert len(list(db.scalars(select(Medicion)))) == 3


def test_colaborador_no_captura(mario, db):
    m = _mci(db)
    mario.post("/indicadores/captura", data={"semana": semana_iso(hoy()), f"valor_{m.id}": "50"})
    assert db.scalar(select(Medicion)) is None


def test_clasificacion_compromisos(db):
    m = _mci(db)
    p = db.scalar(select(Predictiva).where(Predictiva.mci_id == m.id))
    u = _manuel(db)
    sem = semana_iso(hoy())
    def comp(estado):
        c = Compromiso(semana=sem, area_id=m.area_id, responsable_id=u.id, autor_id=u.id, predictiva_id=p.id,
                       texto="x", fecha_limite=hoy(), estado=estado)
        db.add(c)
        db.flush()
        return c
    comp("cumplido")
    c2 = comp("no_cumplido")
    db.add(Remediacion(compromiso_id=c2.id, causa="c", accion="a", nueva_fecha=hoy() - timedelta(days=1), creado_por=u.id))
    c3 = comp("no_cumplido")
    db.add(Remediacion(compromiso_id=c3.id, causa="c", accion="a", nueva_fecha=hoy(), estado="cumplida", creado_por=u.id))
    comp("no_cumplido")
    db.commit()
    pt = indicadores.serie_mci(db, m, 1).puntos[-1]
    assert (pt.cumplidos, pt.remediados, pt.solo_texto) == (1, 1, 2)


def test_diagnostico_con_investigacion_y_seguimiento(admin, db, fake):
    from app.cli import especialistas
    especialistas()
    assert db.scalar(select(PromptArea).where(PromptArea.especialidad.like("%venta consultiva%")))
    fake.respuestas = [
        {"veredicto": "perdiendo", "titular": "Pierde 3 semanas", "lectura": "...", "evaluacion": [],
         "recomendaciones": [{"texto": "Secuencia de 3 toques al webinar", "tecnica": "Social selling", "impacto": "alto"}],
         "investigacion": {"requerida": True, "pregunta": "¿Tasa de conversión de webinar a cita?"}},
        {"veredicto": "perdiendo", "titular": "Pierde 3 semanas (con investigación)", "lectura": "...", "evaluacion": [],
         "recomendaciones": [{"texto": "Secuencia de 3 toques en 48 h", "tecnica": "Social selling", "impacto": "alto"}],
         "investigacion": {"requerida": False}}]
    r = admin.post("/diagnostico/generar", data={"area": "ventas"}, follow_redirects=False)
    assert r.status_code == 303
    d = db.scalar(select(DiagnosticoSemanal))
    assert d.investigacion["fuentes"][0]["url"].startswith("https://") and "48 h" in d.recomendaciones[0].texto
    assert "venta consultiva" in (fake.systems[0] or "")
    rec = d.recomendaciones[0]
    # la recomendación se convierte en compromiso
    parametros.set_(db, "ia_revision_obligatoria", False, None, "prueba")
    db.commit()
    m = _mci(db)
    p = db.scalar(select(Predictiva).where(Predictiva.mci_id == m.id))
    assert "48 h" in admin.get(f"/compromisos?rec={rec.id}").text
    admin.post("/compromisos", data={"area_id": m.area_id, "predictiva_id": p.id, "responsable_id": _manuel(db).id,
                                     "texto": rec.texto, "fecha_limite": hoy().isoformat(), "recomendacion_id": rec.id})
    db.expire_all()
    assert db.get(RecomendacionIA, rec.id).estado == "convertida"
    # la semana siguiente la IA recibe la recomendación con lo que pasó y la evalúa
    rec.semana = semana_iso(hoy() - timedelta(weeks=1))
    d.semana = rec.semana
    db.commit()
    fake.respuestas = [{"veredicto": "en_riesgo", "titular": "t", "lectura": "l",
                        "evaluacion": [{"codigo": f"R{rec.id}", "ejecucion": "parcial", "efecto": "subió 1 pt"}],
                        "recomendaciones": [], "investigacion": {"requerida": False}}]
    admin.post("/diagnostico/generar", data={"area": "ventas"})
    assert f"R{rec.id}" in fake.prompts[-1] and "1 compromiso(s) creados" in fake.prompts[-1]
    db.expire_all()
    assert db.get(RecomendacionIA, rec.id).ejecucion == "parcial"
    assert "subió 1 pt" in admin.get("/diagnostico?area=ventas").text


def test_limite_de_investigaciones(admin, db, fake):
    parametros.set_(db, "investigaciones_por_semana", 0, None, "prueba")
    db.commit()
    fake.respuestas = [{"veredicto": "perdiendo", "lectura": "l", "recomendaciones": [],
                        "investigacion": {"requerida": True, "pregunta": "¿x?"}}]
    admin.post("/diagnostico/generar", data={"area": "ventas"})
    assert fake.investigaciones == [] and db.scalar(select(DiagnosticoSemanal)).investigacion is None


def _crear_compromiso_sesion(db):
    from app.models import Area
    ret = db.scalar(select(Area).where(Area.clave == "retencion"))
    p = db.scalar(select(Predictiva).where(Predictiva.nombre == "Sesiones 1:1 con minuta resuelta"))
    p.requiere_evidencia = True
    mario = db.scalar(select(Usuario).where(Usuario.email == "mpineda@onecore.mx"))
    c = Compromiso(semana=semana_iso(hoy()), area_id=ret.id, responsable_id=mario.id, autor_id=mario.id, predictiva_id=p.id,
                   texto="Sesión 1:1 con la cuenta que renueva en diciembre", fecha_limite=hoy())
    db.add(c)
    db.commit()
    return c


def test_sesion_retencion_evidencia_y_citas(mario, db, fake):
    c = _crear_compromiso_sesion(db)
    parametros.set_(db, "evidencia_sesion_obligatoria", True, None, "prueba")
    db.commit()
    r = mario.post(f"/compromisos/{c.id}/cumplido", follow_redirects=False)
    db.expire_all()
    assert db.get(Compromiso, c.id).estado == "abierto"  # sin evidencia no se cierra
    fake.respuestas = [{"evidencia": {"corresponde": True, "motivo": "Es la sesión 1:1 comprometida"}, "resumen": "r",
                        "riesgo_baja": "alto", "sentimiento": "neutral",
                        "hallazgos": [
                            {"cuadrante": "plataforma", "tema": "Disponibilidad ante caídas de VUCEM",
                             "cita": "cuando VUCEM se cae SAFE no nos deja validar y perdemos el día de despacho completo",
                             "sentimiento": "negativo", "severidad": "alta", "tipo": "queja"},
                            {"cuadrante": "producto", "tema": "Integración MVE con COVE",
                             "cita": "Nos encantaría una app móvil con inteligencia artificial generativa", "sentimiento": "neutral"}],
                        "acuerdos": [{"quien": "nosotros", "texto": "Enviar plan de contingencia de SAFE", "fecha": ""}]}]
    r = mario.post("/sesiones", data={"tipo": "retencion", "fecha": hoy().isoformat(), "compromiso_id": c.id,
                                      "cliente_nombre": "Cliente A", "consentimiento": "true"},
                   files={"archivo": ("sesion.txt", TRANSCRIPCION.encode("utf-8"), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = db.scalar(select(SesionCliente))
    assert s.cifrada and "VUCEM" not in s.transcripcion and "VUCEM" in vozcliente.leer_texto(s)
    assert s.evidencia_verificada and s.citas_descartadas == 1 and len(s.hallazgos) == 1 and s.riesgo_baja == "alto"
    assert "Evidencia verificada" in mario.get(f"/sesiones/{s.id}").text
    mario.post(f"/sesiones/{s.id}/cumplido")
    db.expire_all()
    assert db.get(Compromiso, c.id).estado == "cumplido"
    # la transcripción completa no la ve un líder que no la cargó
    res = vozcliente.resumen(db, 12)
    assert res["n_sesiones"] == 1 and res["prioridad"][0]["tema"].startswith("Disponibilidad") and res["riesgo_alto"] == 1


def test_sesion_objeciones(manuel, db, fake):
    fake.respuestas = [{"evidencia": {"corresponde": False, "motivo": "sin compromiso"}, "resumen": "r",
                        "objeciones": [{"tipo": "precio", "cita": "nos van a pedir justificar el costo frente a otra opción más barata",
                                        "manejo": "explorada", "respuesta": "Comparar con multa", "avanzo": True}],
                        "spin": {"situacion": 4, "problema": 2, "implicacion": 0, "necesidad": 1}, "habla_vendedor": 64,
                        "preguntas_abiertas": 7, "descubrimiento": {"dolor": "rechazos"}, "coaching": "explora antes"}]
    manuel.post("/sesiones", data={"tipo": "objeciones", "fecha": hoy().isoformat(), "cliente_nombre": "Prospecto B",
                                   "consentimiento": "true"},
                files={"archivo": ("s.txt", TRANSCRIPCION.encode("latin-1"), "text/plain")})
    o = db.scalar(select(Objecion))
    assert o and o.tipo == "precio" and o.avanzo
    s = db.scalar(select(SesionCliente))
    assert s.resultado["habla_vendedor"] == 64 and not s.evidencia_verificada
    assert "Comparar con multa" in manuel.get("/voz-cliente?tipo=objeciones").text


def test_circulo_consolidar_votar_decidir(admin, manuel, db, fake):
    autor = _manuel(db)
    m = _mci(db, "ret-corp")
    i1 = Idea(autor_id=autor.id, area_id=m.area_id, titulo="Plan de contingencia SAFE", problema="Caídas de VUCEM",
              origen="queja", estado="validada")
    db.add(i1)
    db.commit()
    admin.post("/circulo", data={"mes": "2026-11"})
    c = db.scalar(select(CirculoInnovacion))
    fake.respuestas = [{"lectura": "Atacan la renovación", "iniciativas": [
        {"titulo": "Contingencia SAFE ante caídas de VUCEM", "descripcion": "d", "impacto": 5, "esfuerzo": 2, "mci": "ret-corp",
         "fuentes": {"ideas": [f"I{i1.id}", "I9999"], "hallazgos": [], "objeciones": []}},
        {"titulo": "Sin fuentes", "descripcion": "x", "impacto": 3, "esfuerzo": 3, "fuentes": {"ideas": ["I9999"]}},
        {"titulo": "Informe de ahorro", "descripcion": "d", "impacto": 4, "esfuerzo": 2, "mci": "ventas-base",
         "fuentes": {"ideas": [f"I{i1.id}"]}}], "sueltas": []}]
    admin.post(f"/circulo/{c.id}/consolidar")
    db.expire_all()
    c = db.get(CirculoInnovacion, c.id)
    assert c.estado == "votacion" and [i.titulo for i in c.iniciativas] == ["Contingencia SAFE ante caídas de VUCEM", "Informe de ahorro"]
    a, b = c.iniciativas
    assert a.fuentes["ideas"] == [i1.id] and a.cuadrante == "Ganancia rápida"
    # reglas: máximo 3 por iniciativa y 5 en total
    manuel.post(f"/circulo/{c.id}/votar", data={"iniciativa_id": a.id, "puntos": 9})
    manuel.post(f"/circulo/{c.id}/votar", data={"iniciativa_id": b.id, "puntos": 3})
    db.expire_all()
    from app.models import Voto
    votos = {v.iniciativa_id: v.puntos for v in db.scalars(select(Voto))}
    assert votos == {a.id: 3}  # el segundo voto excedía los 5 puntos
    assert "Los resultados se muestran al cerrar" in manuel.get(f"/circulo/{c.id}").text
    admin.post(f"/circulo/{c.id}/cerrar-votacion")
    t = admin.get(f"/circulo/{c.id}").text
    assert "Decisiones del círculo" in t and "Contingencia SAFE" in t
    assert admin.get(f"/circulo/{c.id}/fuentes/{a.id}").status_code == 200
    assert manuel.post(f"/circulo/{c.id}/decisiones", data={}).status_code == 403
    admin.post(f"/circulo/{c.id}/decisiones", data={f"decision_{a.id}": "estrategia", f"responsable_{a.id}": str(autor.id),
                                                   f"decision_{b.id}": "banco"})
    db.expire_all()
    est = db.scalar(select(Estrategia))
    assert est and est.estado == "activa" and est.mci_id == m.id and est.responsable_id == autor.id
    assert db.get(Idea, i1.id).estado == "en_curso" and db.get(CirculoInnovacion, c.id).estado == "cerrado"
    assert "Ver estrategia creada" in admin.get(f"/circulo/{c.id}").text


def test_ventas_automaticas_desde_panel_ayf(admin, mario, db):
    from datetime import date
    from app.cli import ventas_auto_cmd
    from app.models import ClienteEstatus, VentaClienteMes
    from app.services import ventas_auto
    # base: ene-mar con 2 recurrentes de 100k c/u y un cliente no recurrente; hoy: un recurrente sube a 150k
    hoy_ = hoy()
    mes_act = f"{hoy_.year}-{hoy_.month:02d}"
    for per in ("2026-01", "2026-02", "2026-03"):
        db.add(VentaClienteMes(periodo=per, rfc="AAA", nombre="A", total_mxn=100000, facturas=1))
        db.add(VentaClienteMes(periodo=per, rfc="BBB", nombre="B", total_mxn=100000, facturas=1))
        db.add(VentaClienteMes(periodo=per, rfc="ZZZ", nombre="Z", total_mxn=50000, facturas=1))
    db.add(VentaClienteMes(periodo=mes_act, rfc="AAA", nombre="A", total_mxn=150000, facturas=1))
    db.add(VentaClienteMes(periodo=ventas_auto._mes_anterior(mes_act), rfc="BBB", nombre="B", total_mxn=100000, facturas=1))
    db.add(VentaClienteMes(periodo=ventas_auto._mes_anterior(mes_act), rfc="ZZZ", nombre="Z", total_mxn=300000, facturas=1))
    db.add_all([ClienteEstatus(rfc="AAA", recurrente=True), ClienteEstatus(rfc="BBB", recurrente=True),
                ClienteEstatus(rfc="ZZZ", recurrente=False)])
    db.commit()
    ventas_auto_cmd()
    db.expire_all()
    rec = _mci(db)
    v = rec.versiones[-1]
    v.fecha_meta = date(hoy_.year + 1, 6, 30)
    db.commit()
    r = ventas_auto.resumen(db, rec)
    # MRR: AAA 150k (mes actual) + BBB 100k (mes anterior) = 250k contra base 200k = +25%
    assert round(r.pct) == 25 and r.base == 200000 and r.actual == 250000
    assert r.meta_monto == 270000 and r.falta == 20000 and r.necesario_mensual > 0
    tot = _mci(db, "ventas-facturacion")
    rt = ventas_auto.resumen(db, tot)
    assert rt.base == 250000 and round(rt.pct) == 60  # último mes cerrado 400k vs 250k
    assert db.scalar(select(Medicion).where(Medicion.fuente == "cfdi")) is not None
    # todos ven % y crecimiento necesario; los montos solo con permiso confidencial
    t_admin = admin.get("/?area=ventas").text
    t_mario = mario.get("/?area=ventas").text
    assert "Necesitas crecer" in t_mario and "250,000" not in t_mario and "Actual $250,000" in t_admin
    # la captura manual ya no ofrece los indicadores automáticos
    assert f'name="valor_{_mci(db).id}"' not in admin.get("/indicadores/captura").text  # automático: no se captura


def test_vista_consolidada_por_area(admin, db):
    t = admin.get("/?area=ventas").text
    assert "Palancas de Ventas" not in t  # palancas = medidas predictivas
    for txt in ("Metas y medidas predictivas · 8 semanas", "mci-row", "pred-row", "Citas por LinkedIn",
                "Nuevo compromiso", "Expandir todo", "Voz del cliente · 6 semanas", "Ideas del área"):
        assert txt in t, txt
    t = admin.get("/").text
    assert "Resumen por área" in t and "Ver área" in t


def test_ventas_por_frentes_con_panel_ayf_informativo(admin, mario, db):
    from app.cli import ventas_auto_cmd, ventas_frentes
    from app.models import ClienteEstatus, VentaClienteMes
    from app.services import ventas_auto
    mes = f"{hoy().year}-{hoy().month:02d}"
    db.add_all([VentaClienteMes(periodo="2026-01", rfc="AAA", nombre="A", total_mxn=100000, facturas=1),
                VentaClienteMes(periodo=mes, rfc="AAA", nombre="A", total_mxn=130000, facturas=1),
                ClienteEstatus(rfc="AAA", recurrente=True)])
    db.commit()
    ventas_auto_cmd()
    ventas_frentes()
    ventas_frentes()  # idempotente
    db.expire_all()
    assert not _mci(db).activo and not _mci(db, "ventas-facturacion").activo
    cs, nv = _mci(db, "ventas-crossselling"), _mci(db, "ventas-nuevos")
    assert cs.activo and nv.activo and len(cs.predictivas) == 3 and len(nv.predictivas) == 3
    assert not ventas_auto.es_auto(cs)
    t_admin, t_mario = admin.get("/?area=ventas").text, mario.get("/?area=ventas").text
    assert "CrossSelling · clientes actuales cotizados" in t_admin and "Panel AyF · informativo" in t_admin
    assert "Actual $130,000" in t_admin and "130,000" not in t_mario
    assert f'name="valor_{cs.id}"' in admin.get("/indicadores/captura").text


def test_compromisos_dentro_de_cada_predictiva(admin, db):
    from app.models import Area, Predictiva, Usuario
    from datetime import timedelta
    v = db.scalar(select(Area).where(Area.clave == "ventas"))
    p = db.scalar(select(Predictiva).where(Predictiva.clave.like("ventas%")))
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    largo = "Revisar si SAFE permite descargar de forma masiva los reportes " * 6
    db.add_all([Compromiso(semana=semana_iso(hoy()), area_id=v.id, responsable_id=m.id, autor_id=m.id, predictiva_id=p.id,
                           texto=largo, fecha_limite=hoy() + timedelta(days=2)),
                Compromiso(semana=indicadores.semanas(2)[0], area_id=v.id, responsable_id=m.id, autor_id=m.id,
                           predictiva_id=p.id, texto="Compromiso de la semana pasada", fecha_limite=hoy() - timedelta(days=5))])
    db.commit()
    t = admin.get("/?area=ventas").text
    assert f'data-sub="sub-{p.id}"' in t and f'id="sub-{p.id}"' in t and "Ver completo" in t
    assert f'data-open="w-{p.id}-{indicadores.semanas(2)[0]}"' in t and "Compromiso de la semana pasada" in t
    assert f'data-fill-nc-pred="{p.id}"' in t and 'id="nuevo-compromiso"' in t
    assert "Compromisos de la semana</h2>" not in t

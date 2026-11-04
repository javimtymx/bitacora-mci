from sqlalchemy import select

from app.models import Hallazgo, SesionCliente
from app.services.util import hoy

TX = ("Vendedor: Gracias por recibirnos, Laura. Laura Méndez (gerente de comercio exterior): Lo que más nos importa es "
      "no pagar multas por pedimentos mal declarados. Hoy validamos todo en Excel y nos toma dos días por semana. "
      "Vendedor: Con SAFE validamos el pedimento antes del pago y les ofrecemos un piloto de 30 días sin costo. "
      "Laura: Me gustó mucho lo de SAFE, eso nos sirve. También tenemos un problema con los permisos de la Secretaría "
      "de Economía, los cupos se nos vencen y nadie avisa. Pedro Ruiz (analista, pruiz@cliente.com): y el expediente "
      "para la auditoría lo armamos a mano cada año. ") * 3


def _res():
    return {"evidencia": {"corresponde": True, "motivo": "ok"}, "resumen": "Interés alto en SAFE.", "interes_general": "alto",
            "valora": [{"texto": "Evitar multas", "cita": "no pagar multas por pedimentos mal declarados"}],
            "productos_interes": [{"modulo": "safe", "interes": "alto", "cita": "Me gustó mucho lo de SAFE, eso nos sirve"}],
            "encajes": [
                {"tipo": "frustracion", "texto": "Validación manual en Excel", "cita": "validamos todo en Excel y nos toma dos días por semana",
                 "encaje": "cubierto", "modulo": "SAFE", "oportunidad": ""},
                {"tipo": "frustracion", "texto": "Cupos de Economía que vencen sin aviso",
                 "cita": "los cupos se nos vencen y nadie avisa", "encaje": "sin_encaje", "modulo": "",
                 "oportunidad": "Alertas de vencimiento de permisos y cupos"},
                {"tipo": "trabajo", "texto": "Inventada", "cita": "esto nunca se dijo en la sesión", "encaje": "sin_encaje"}],
            "ofertas": [{"oferta": "Piloto de SAFE", "quien": "Vendedor", "cita": "les ofrecemos un piloto de 30 días sin costo"}],
            "participantes": [{"nombre": "Laura Méndez", "lado": "cliente", "rol": "gerente de comercio exterior"},
                              {"nombre": "Pedro Ruiz", "email": "pruiz@cliente.com", "lado": "cliente", "rol": "analista"}],
            "siguiente_paso": "Demo de SAFE el jueves", "acuerdos": []}


def test_sesion_de_venta_nueva(manuel, db, fake):
    fake.respuestas = [_res()]
    r = manuel.post("/sesiones", data={"tipo": "venta_nueva", "fecha": hoy().isoformat(), "cliente_nombre": "Manufacturas del Norte",
                                       "consentimiento": "true", "p_nombre": ["Laura Méndez", "Javier"],
                                       "p_email": ["laura@mdn.mx", "jmartinez@onecore.mx"], "p_lado": ["cliente", "onecore"]},
                    files={"archivo": ("v.txt", TX.encode("utf-8"), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    s = db.scalar(select(SesionCliente).where(SesionCliente.tipo == "venta_nueva"))
    db.refresh(s)
    vn = s.resultado["venta"]
    assert vn["productos_interes"][0]["modulo"] == "SAFE"
    assert len(vn["encajes"]) == 2 and s.citas_descartadas == 1  # la cita inventada se descarta
    assert [e["encaje"] for e in vn["encajes"]] == ["cubierto", "sin_encaje"]
    # la frustración sin encaje se vuelve hallazgo de producto (va a Ideas y al Círculo)
    h = db.scalar(select(Hallazgo).where(Hallazgo.sesion_id == s.id))
    assert h and "vencimiento" in h.tema and vn["encajes"][1]["hallazgo_id"] == h.id
    # participantes: lo capturado se respeta y se agrega lo detectado con su correo
    pp = {p["nombre"]: p for p in s.participantes_det}
    assert pp["Laura Méndez"]["email"] == "laura@mdn.mx" and pp["Laura Méndez"]["rol"] == "gerente de comercio exterior"
    assert pp["Pedro Ruiz"]["email"] == "pruiz@cliente.com" and pp["Pedro Ruiz"]["origen"] == "detectado"
    assert pp["Javier"]["lado"] == "onecore"
    t = manuel.get(f"/sesiones/{s.id}").text
    for x in ("Lo que valora el cliente", "Productos que le interesaron", "Cubierto por OneCore", "Sin encaje",
              "Lo que se le ofreció", "Demo de SAFE el jueves", "pruiz@cliente.com", "Manufacturas del Norte"):
        assert x in t, x
    # editar participantes
    manuel.post(f"/sesiones/{s.id}/participantes", data={"p_nombre": ["Laura Méndez", ""], "p_email": ["correo-malo", ""],
                                                          "p_lado": ["cliente", "cliente"], "p_rol": ["Directora", ""]})
    db.refresh(s)
    assert s.participantes_det == [{"nombre": "Laura Méndez", "email": "", "lado": "cliente", "rol": "Directora", "origen": "captura"}]


def test_prospecto_oculto_sin_permiso(mario, manuel, db, fake):
    fake.respuestas = [_res()]
    manuel.post("/sesiones", data={"tipo": "venta_nueva", "fecha": hoy().isoformat(), "cliente_nombre": "Prospecto Secreto",
                                   "consentimiento": "true"}, files={"archivo": ("v.txt", TX.encode("utf-8"), "text/plain")})
    s = db.scalar(select(SesionCliente).where(SesionCliente.tipo == "venta_nueva"))
    t = mario.get(f"/sesiones/{s.id}")
    if t.status_code == 200:  # si su rol le permite ver la sesión, no ve el nombre del prospecto ni correos
        assert "Prospecto Secreto" not in t.text and "pruiz@cliente.com" not in t.text and f"Prospecto #{s.id:04d}" in t.text


def test_idea_desde_voz_del_cliente_con_texto_completo_y_enlace(manuel, db, fake):
    from app.models import Idea
    fake.respuestas = [_res()]
    manuel.post("/sesiones", data={"tipo": "venta_nueva", "fecha": hoy().isoformat(), "cliente_nombre": "Crown",
                                   "consentimiento": "true"}, files={"archivo": ("v.txt", TX.encode("utf-8"), "text/plain")})
    s = db.scalar(select(SesionCliente).where(SesionCliente.cliente_nombre == "Crown"))
    h = db.scalar(select(Hallazgo).where(Hallazgo.sesion_id == s.id))
    manuel.post(f"/hallazgos/{h.id}/idea")
    i = db.scalar(select(Idea).order_by(Idea.id.desc()))
    assert i.sesion_id == s.id
    t = manuel.get("/ideas?filtro=mias").text
    assert f'href="/sesiones/{s.id}"' in t and "Ver la sesión con el cliente" in t
    assert h.cita in t  # el texto completo, sin recortar

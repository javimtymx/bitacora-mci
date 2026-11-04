from datetime import timedelta

from sqlalchemy import select

from app.models import Compromiso, Predictiva, Usuario
from app.services import ia
from app.services.util import hoy


def _form(db, texto):
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    p = db.scalar(select(Predictiva).where(Predictiva.clave.like("ventas%")))
    return {"area_id": m.area_id, "predictiva_id": p.id, "responsable_id": m.id, "texto": texto,
            "fecha_limite": (hoy() + timedelta(days=2)).isoformat()}


def test_filtro_basico():
    assert ia.debilidad_basica("dar seguimiento")
    assert ia.debilidad_basica("ventas ventas ventas ventas ventas ventas ventas ventas")
    assert ia.debilidad_basica("1234 5678 #### 9999 0000 ---- ++++ 11")
    assert ia.debilidad_basica("Enviar 15 mensajes a prospectos IMMEX y agendar 2 citas antes del viernes") is None


def test_muy_debil_no_se_guarda_como_esta(manuel, db, fake):
    fake.respuestas = [{"veredicto": "ok", "smart": {"especifico": True, "medible": True, "alcanzable": True,
                                                       "relevante": True, "tiempo": True},
                        "observaciones": [], "sugerencia": "Enviar 15 mensajes a prospectos IMMEX y agendar 2 citas antes del viernes"}]
    d = _form(db, "dar seguimiento")
    r = manuel.post("/api/ia/compromiso", data=d).json()
    assert r["veredicto"] == "muy_debil" and "corto" in r["observaciones"][0]  # la regla manda aunque la IA dijera ok
    x = manuel.post("/compromisos", data={**d, "revision_id": r["revision_id"], "decision": "propio", "motivo": "así"})
    assert x.status_code == 400
    x = manuel.post("/compromisos", data={**d, "revision_id": r["revision_id"], "decision": "sugerencia"}, follow_redirects=False)
    assert x.status_code == 303  # con la sugerencia SMART sí
    assert db.scalar(select(Compromiso).order_by(Compromiso.id.desc())).texto.startswith("Enviar 15 mensajes")


def test_ok_sin_smart_completo_baja_a_mejorar(manuel, db, fake):
    fake.respuestas = [{"veredicto": "ok", "smart": {"especifico": True, "medible": False, "alcanzable": True,
                                                       "relevante": True, "tiempo": True}, "observaciones": [], "sugerencia": ""}]
    r = manuel.post("/api/ia/compromiso", data=_form(db, "Llamar a los clientes del módulo SAFE para revisar su uso esta semana")).json()
    assert r["veredicto"] == "mejorar" and r["smart"]["medible"] is False


def test_sin_ia_tampoco_se_guarda_un_compromiso_debil(manuel, db):
    class SinIA:
        disponible = False
    ia.set_cliente(SinIA())
    try:
        x = manuel.post("/compromisos", data={**_form(db, "revisar"), "decision": "propio"})
        assert x.status_code == 400 and "muy débil" in x.text
        ok = manuel.post("/compromisos", data={**_form(db, "Enviar 15 mensajes a prospectos IMMEX y agendar 2 citas antes del viernes"),
                                               "decision": "propio"}, follow_redirects=False)
        assert ok.status_code == 303
    finally:
        ia.set_cliente(None)


def test_ayudame_a_redactarlo(manuel, db, fake):
    fake.respuestas = [{"opciones": [{"texto": "Agendar 4 análisis 1:1 con clientes IMMEX y presentar 2 antes del viernes",
                                      "por_que": "Cantidad y fecha verificables"}], "preguntas": ["¿Con qué clientes?"]}]
    r = manuel.post("/api/ia/compromiso/ayuda", data=_form(db, "")).json()
    assert r["ok"] and r["opciones"][0]["texto"].startswith("Agendar 4") and r["preguntas"]

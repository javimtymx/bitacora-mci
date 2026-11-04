import base64
from datetime import timedelta

from sqlalchemy import select

from app.models import Compromiso, Edicion, Idea, Predictiva, Usuario
from app.services.util import hoy, semana_iso

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _comp(db, texto="Enviar 15 mensajes a prospectos IMMEX y agendar 2 citas antes del viernes"):
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    p = db.scalar(select(Predictiva).where(Predictiva.clave.like("ventas%")))
    c = Compromiso(semana=semana_iso(hoy()), area_id=m.area_id, responsable_id=m.id, autor_id=m.id, predictiva_id=p.id,
                   texto=texto, fecha_limite=hoy() + timedelta(days=2))
    db.add(c)
    db.commit()
    return c


def test_cierre_exige_evidencia_y_guardia_la_revisa(manuel, mario, db, fake):
    c = _comp(db)
    r = manuel.post(f"/compromisos/{c.id}/cumplido", data={"comentario": "listo"}, follow_redirects=False)
    db.refresh(c)
    assert r.status_code == 303 and c.estado == "abierto"  # sin evidencia no se cierra
    r = manuel.post(f"/compromisos/{c.id}/cumplido", data={"comentario": "va"},
                    files=[("archivos", ("pagina.html", b"<script>x</script>", "text/html"))], follow_redirects=False)
    db.refresh(c)
    assert c.estado == "abierto"  # tipo no permitido
    fake.respuestas = [{"veredicto": "dudoso", "confianza": "media", "motivo": "La captura no muestra los 15 envíos.",
                        "cumple": ["Se ve un correo enviado"], "falta": ["13 envíos y las 2 citas"]}]
    r = manuel.post(f"/compromisos/{c.id}/cumplido", data={"comentario": "Envié la campaña"},
                    files=[("archivos", ("captura.png", PNG, "image/png")),
                           ("archivos", ("lista.txt", "prospecto 1\nprospecto 2".encode(), "text/plain"))],
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    c = db.get(Compromiso, c.id)
    assert c.estado == "cumplido" and len(c.evidencias) == 2
    assert c.cierre_veredicto == "dudoso" and "15" in c.cierre_detalle["motivo"]  # verificación en segundo plano
    bloques = fake.prompts[-1]
    assert any(b.get("type") == "image" for b in bloques) and any("prospecto 1" in b.get("text", "") for b in bloques)
    t = manuel.get(f"/compromisos/{c.id}").text
    assert "Cierre con sospecha" in t and "13 envíos y las 2 citas" in t
    assert "Cierre con sospecha" in manuel.get("/compromisos?estado=cerrados").text
    # descarga: el dueño sí, alguien de otra área no
    ev = c.evidencias[0]
    d = manuel.get(f"/evidencias/{ev.id}")
    assert d.status_code == 200 and d.content == PNG and "attachment" in d.headers["content-disposition"]
    assert mario.get(f"/evidencias/{ev.id}").status_code == 404
    # evidencia adicional después del cierre: se vuelve a verificar
    fake.respuestas = [{"veredicto": "concluido", "motivo": "Ya se ven los 15 envíos y las citas."}]
    manuel.post(f"/compromisos/{c.id}/evidencias", files=[("archivos", ("reporte.csv", b"envio,15\ncitas,2", "text/csv"))])
    db.expire_all()
    assert db.get(Compromiso, c.id).cierre_veredicto == "concluido"


def test_editar_compromiso_deja_historial(manuel, db):
    c = _comp(db)
    r = manuel.post(f"/compromisos/{c.id}/editar", data={"texto": "revisar", "fecha_limite": c.fecha_limite.isoformat(),
                                                         "responsable_id": c.responsable_id, "predictiva_id": c.predictiva_id})
    db.refresh(c)
    assert c.texto.startswith("Enviar 15")  # quedaría muy débil: no se guarda
    nuevo = "Enviar 20 mensajes a prospectos IMMEX de Monterrey y agendar 3 citas antes del jueves"
    manuel.post(f"/compromisos/{c.id}/editar", data={"texto": nuevo, "fecha_limite": (c.fecha_limite + timedelta(days=1)).isoformat(),
                                                     "responsable_id": c.responsable_id, "predictiva_id": c.predictiva_id,
                                                     "motivo": "Se amplió la lista"})
    db.refresh(c)
    assert c.texto == nuevo
    e = db.scalar(select(Edicion).where(Edicion.entidad == "compromiso", Edicion.entidad_id == c.id))
    assert e and e.antes["texto"].startswith("Enviar 15") and set(e.antes) == {"texto", "fecha_limite"} and e.motivo
    t = manuel.get(f"/compromisos/{c.id}").text
    assert 'data-open="hist-comp"' in t and "Se amplió la lista" in t and "Enviar 15 mensajes" in t


def test_editar_idea_con_permisos_e_historial(manuel, mario, db):
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    i = Idea(autor_id=m.id, area_id=m.area_id, titulo="Alertas de cupos", problema="Los cupos vencen sin aviso",
             origen="queja")
    db.add(i)
    db.commit()
    d = {"idea_id": i.id, "titulo": "Alertas de vencimiento de cupos y permisos",
         "problema": "Los cupos de Economía vencen sin aviso y se detiene la importación", "beneficio": "Cero paros",
         "area_id": m.area_id, "motivo": "Complemento con la sesión"}
    assert mario.post("/ideas/editar", data=d).status_code == 403  # otro líder, otra área
    manuel.post("/ideas/editar", data=d)
    db.refresh(i)
    assert i.titulo.startswith("Alertas de vencimiento")
    t = manuel.get("/ideas?filtro=mias").text
    assert f'data-open="hist-idea-{i.id}"' in t and "Los cupos vencen sin aviso" in t

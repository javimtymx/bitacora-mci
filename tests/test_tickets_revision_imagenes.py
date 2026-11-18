"""Revisión de tickets con permiso «Revisa tickets de otros», imágenes dentro del texto y correos solo por estatus."""
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Ticket, TicketComentario, TicketImagen, Usuario
from app.services import tickets as tk
from conftest import login

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _u(db, email):
    return db.scalar(select(Usuario).where(Usuario.email == email))


@pytest.fixture
def correos(monkeypatch):
    from app.services import correo
    enviados = []
    monkeypatch.setattr(correo, "enviar", lambda para, asunto, cuerpo, enlace=None, **kw: enviados.append(
        {"para": para, "asunto": asunto, "cuerpo": cuerpo}) or "simulado")
    return enviados


def _en_revision(db):
    jm, manuel = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    t = tk.crear(db, manuel, {"descripcion": "Al timbrar la factura en CIVA sale error de sello en todas", "producto": "CIVA",
                              "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: sello"}, [], validacion="completo",
                 faltantes=[], conversacion=[], prompt_version=None)
    tk.triage(db, jm, t, "P2", jm)
    tk.planear(db, jm, t, horas=2, fecha_entrega=date.today(), asignado=jm)
    tk.resolver(db, jm, t, tipo_cierre="corregido", causa="El certificado del cliente venció",
                solucion="Se cargó el certificado vigente y se reintentó")
    db.commit()
    return t


def test_revision_con_permiso_de_revisar_tickets_de_otros(admin, db, correos):
    t = _en_revision(db)
    eg = _u(db, "egarza@onecore.mx")
    erick = login(TestClient(app), "egarza@onecore.mx")
    # sin permiso: no puede cerrar ni regresar
    r = erick.post(f"/tickets/{t.id}/aceptar", data={"comentario": "Confirmó por teléfono"}, headers={"Accept": "application/json"})
    assert r.status_code == 400 and "Revisa tickets de otros" in r.json()["mensaje"]
    # Dirección le da el permiso desde Usuarios
    from app.web import admin as adm  # noqa: F401
    eg.revisa_tickets = True
    db.commit()
    assert "Revisa tickets de otros" in erick.get("/perfil").text
    assert 'data-revisa=""' in admin.get("/tickets-tablero").text  # Dirección sin el permiso: tarjeta fija
    jm_ = _u(db, "jmartinez@onecore.mx")
    jm_.revisa_tickets = True
    db.commit()
    tab = admin.get("/tickets-tablero").text
    assert 'data-revisa="otro"' in tab and "Regresar a En curso" in tab
    assert "Regresar a En curso" in erick.get(f"/tickets/{t.id}").text
    # en lugar de otro, el motivo es obligatorio
    r = erick.post(f"/tickets/{t.id}/aceptar", data={"comentario": ""}, headers={"Accept": "application/json"})
    assert r.status_code == 400 and "motivo" in r.json()["mensaje"]
    # regresar a En curso desde el Kanban (JSON) con motivo
    correos.clear()
    r = erick.post(f"/tickets/{t.id}/rechazar", data={"motivo": "Falta probar la sucursal 2 antes de cerrarlo"},
                   headers={"Accept": "application/json"})
    assert r.json()["ok"]
    db.refresh(t)
    assert t.estado == "en_curso"
    assert any("mcoronado@onecore.mx" in e["para"] and "En curso" in e["asunto"] for e in correos)  # el solicitante se entera
    # se resuelve otra vez y lo cierra en su lugar
    jm = _u(db, "jmartinez@onecore.mx")
    tk.resolver(db, jm, t, tipo_cierre="corregido", causa="Faltaba el certificado de la sucursal 2",
                solucion="Se cargó también el certificado de la sucursal 2")
    db.commit()
    correos.clear()
    r = erick.post(f"/tickets/{t.id}/aceptar", data={"comentario": "Manuel confirmó por teléfono que ya timbra"},
                   headers={"Accept": "application/json"})
    assert r.json()["ok"]
    db.refresh(t)
    assert t.estado == "cerrado" and t.resolucion["aceptado_en_nombre"] and "teléfono" in t.resolucion["motivo_cierre"]
    assert any(c.texto.startswith("Cerrado por Erick") for c in t.comentarios)
    assert any("mcoronado@onecore.mx" in e["para"] and "en tu lugar" in e["cuerpo"] for e in correos)


def test_admin_otorga_permiso_en_el_perfil(admin, db):
    eg = _u(db, "egarza@onecore.mx")
    html = admin.get("/admin/usuarios").text
    assert "Revisa tickets de otros" in html
    admin.post(f"/admin/usuarios/{eg.id}", data={"nombre": eg.nombre, "rol": eg.rol, "menus_form": "1",
                                                  "revisa_tickets": "true", "recibe_correos": "true"})
    db.refresh(eg)
    assert eg.revisa_tickets


def test_imagenes_dentro_de_descripcion_y_comentarios(admin, db):
    mc = login(TestClient(app), "mcoronado@onecore.mx")
    # solo imágenes reales (por firma), no otros archivos con nombre .png
    r = mc.post("/tickets-img", files={"imagen": ("x.png", io.BytesIO(b"%PDF-1.4 nope"), "image/png")})
    assert r.status_code == 400
    r = mc.post("/tickets-img", files={"imagen": ("captura.png", io.BytesIO(PNG), "image/png")}).json()
    assert r["ok"] and r["md"] == f"![imagen](/tickets-img/{r['id']})"
    # antes de ligarse al ticket solo la ve quien la subió
    assert mc.get(r["url"]).status_code == 200
    assert login(TestClient(app), "egarza@onecore.mx").get(r["url"]).status_code == 404
    manuel = _u(db, "mcoronado@onecore.mx")
    t = tk.crear(db, manuel, {"descripcion": f"Sale error de sello al timbrar:\n{r['md']}\ny después se congela", "producto": "CIVA",
                              "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: sello"}, [], validacion="completo",
                 faltantes=[], conversacion=[], prompt_version=None)
    db.commit()
    img = db.get(TicketImagen, r["id"])
    assert img.ticket_id == t.id and len(t.anexos) == 0  # no es anexo
    html = admin.get(f"/tickets/{t.id}").text
    assert f'<img src="/tickets-img/{img.id}"' in html and "y después se congela" in html
    assert admin.get(f"/tickets-img/{img.id}").headers["content-type"] == "image/png"
    # en un comentario, dentro del hilo
    r2 = admin.post("/tickets-img", files={"imagen": ("c.png", io.BytesIO(PNG), "image/png")}, data={"ticket_id": t.id}).json()
    admin.post(f"/tickets/{t.id}/comentar", data={"texto": f"Así se ve ahora:\n{r2['md']}"})
    html = admin.get(f"/tickets/{t.id}").text
    assert f'<img src="/tickets-img/{r2["id"]}"' in html and "Insertar imagen" not in html.split("tkt-hilo")[0]
    # no se puede robar una imagen de otro ticket referenciándola
    otro = tk.crear(db, manuel, {"descripcion": f"Otro problema distinto con la imagen ![imagen](/tickets-img/{img.id})",
                                 "producto": "CIVA", "tipo": "error", "impacto": "bloquea", "titulo": "Otro"}, [],
                    validacion="completo", faltantes=[], conversacion=[], prompt_version=None)
    db.commit()
    db.refresh(img)
    assert img.ticket_id == t.id
    # el texto se escapa: solo el código de imagen se vuelve <img>
    admin.post(f"/tickets/{otro.id}/comentar", data={"texto": "<script>alert(1)</script> ![x](/tickets-img/1)\" onerror=alert(1)"})
    html = admin.get(f"/tickets/{otro.id}").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_correos_solo_por_cambio_de_estatus(admin, db, correos):
    jm, manuel = _u(db, "jmartinez@onecore.mx"), _u(db, "mcoronado@onecore.mx")
    t = tk.crear(db, manuel, {"descripcion": "Al timbrar la factura en CIVA sale error de sello en todas", "producto": "CIVA",
                              "tipo": "error", "impacto": "bloquea", "titulo": "CIVA: sello"}, [], validacion="completo",
                 faltantes=[], conversacion=[], prompt_version=None)
    tk.triage(db, jm, t, "P2", jm)
    tk.planear(db, jm, t, horas=2, fecha_entrega=date.today(), asignado=jm)
    db.commit()
    correos.clear()
    # comentarios: sin correo (de ninguno de los dos lados)
    admin.post(f"/tickets/{t.id}/comentar", data={"texto": "Estoy revisando el certificado"})
    login(TestClient(app), "mcoronado@onecore.mx").post(f"/tickets/{t.id}/comentar", data={"texto": "Gracias, quedo atento"})
    # replanear sin cambio de estatus: sin correo
    admin.post(f"/tickets/{t.id}/planear", data={"horas": "3", "fecha_entrega": date.today().isoformat(),
                                                 "asignado_id": jm.id, "motivo": "Se revisa también la sucursal 2"})
    db.refresh(t)
    assert t.estado == "planeado" and float(t.horas_estimadas) == 3
    assert correos == []
    # cambio de estatus: sí
    admin.post(f"/tickets/{t.id}/mover", data={"estado": "en_curso"})
    assert len(correos) == 1 and "En curso" in correos[0]["asunto"]

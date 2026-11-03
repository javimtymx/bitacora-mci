from datetime import timedelta

from sqlalchemy import select

from app.models import AccionAgente, Compromiso, Predictiva, SolicitudEliminacion, Usuario
from app.services import seguimiento
from app.services.util import hoy, semana_iso


def _comp(db, texto="Compromiso duplicado"):
    m = db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))
    p = db.scalar(select(Predictiva).where(Predictiva.clave.like("ventas%")))
    c = Compromiso(semana=semana_iso(hoy()), area_id=m.area_id, responsable_id=m.id, autor_id=m.id, predictiva_id=p.id,
                   texto=texto, fecha_limite=hoy() + timedelta(days=2))
    db.add(c)
    db.flush()
    db.add(AccionAgente(tipo="recordatorio", origen="agente", titulo="Recordar", destinatarios=[m.id],
                        entidad_tipo="compromiso", entidad_id=c.id, estado="propuesta"))
    db.commit()
    return c


def test_solicitud_y_autorizacion_de_eliminacion(admin, manuel, db):
    c = _comp(db)
    r = manuel.post(f"/compromisos/{c.id}/eliminar", data={"motivo": "corto"}, follow_redirects=False)
    assert r.status_code == 303 and db.scalar(select(SolicitudEliminacion)) is None
    manuel.post(f"/compromisos/{c.id}/eliminar", data={"motivo": "Se capturó dos veces, el bueno es el otro"})
    s = db.scalar(select(SolicitudEliminacion))
    assert s and s.estado == "pendiente"
    # sigue visible y marcado mientras no se autorice
    assert "Eliminación solicitada" in manuel.get("/compromisos").text
    assert manuel.get("/compromisos/eliminaciones").status_code == 403
    assert manuel.post(f"/compromisos/eliminaciones/{s.id}/aprobar", data={}).status_code == 403
    # el admin ve la cintilla con el enlace
    t = admin.get("/").text
    assert "Revisar solicitudes de eliminación" in t and 'href="/compromisos/eliminaciones"' in t
    assert "Se capturó dos veces" in admin.get("/compromisos/eliminaciones").text
    r = admin.post(f"/compromisos/eliminaciones/{s.id}/aprobar", data={"comentario": "ok"}, follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    with SessionLocal() as nueva:  # como en cada petición: sesión nueva
        assert nueva.get(Compromiso, c.id) is None  # ya no existe para el resto de la app
        assert nueva.get(Compromiso, c.id, execution_options={"include_archivados": True}) is not None
    db.expire_all()
    arch = db.get(Compromiso, c.id, execution_options={"include_archivados": True})
    assert arch.archivado and arch.archivado_en
    assert db.scalar(select(AccionAgente).where(AccionAgente.entidad_id == c.id)).estado == "rechazada"
    assert "Compromiso duplicado" not in manuel.get("/compromisos").text
    assert "Compromiso duplicado" in manuel.get("/compromisos/archivados").text
    assert manuel.get(f"/compromisos/{c.id}").status_code == 404
    assert "Revisar solicitudes de eliminación" not in admin.get("/").text
    db.expunge_all()
    comps = list(db.scalars(select(Compromiso)))
    assert all(x.id != c.id for x in comps) and seguimiento.cumplimiento(comps, []) is not None


def test_rechazo_mantiene_el_compromiso(admin, manuel, db):
    c = _comp(db, "Compromiso que sí va")
    manuel.post(f"/compromisos/{c.id}/eliminar", data={"motivo": "Ya no me interesa hacerlo"})
    s = db.scalar(select(SolicitudEliminacion))
    admin.post(f"/compromisos/eliminaciones/{s.id}/rechazar", data={"comentario": "Debe cumplirse; es de la MCI"})
    db.expire_all()
    assert db.get(Compromiso, c.id) is not None and db.get(SolicitudEliminacion, s.id).estado == "rechazada"
    assert "Compromiso que sí va" in manuel.get("/compromisos").text

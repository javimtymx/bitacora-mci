from sqlalchemy import select

from app.models import Medicion, Mci, Predictiva, Usuario
from app.services import accesos
from app.services.util import hoy, semana_iso


def _manuel(db):
    return db.scalar(select(Usuario).where(Usuario.email == "mcoronado@onecore.mx"))


def test_rutas_se_mapean_a_su_menu():
    assert accesos.menu_de_ruta("/") == "inicio"
    assert accesos.menu_de_ruta("/kardex/3") == "inicio"
    assert accesos.menu_de_ruta("/voz-cliente") == "voz"
    assert accesos.menu_de_ruta("/sesiones/4/analizar") == "voz"
    assert accesos.menu_de_ruta("/api/ia/compromiso") == "compromisos"
    assert accesos.menu_de_ruta("/reportes/compromisos.xlsx") == "reportes"
    assert accesos.menu_de_ruta("/perfil") is None


def test_quitar_menus_bloquea_menu_y_direccion(admin, manuel, db):
    m = _manuel(db)
    permitidos = [x.clave for x in accesos.MENUS if accesos.por_rol(m.rol, x) and x.clave not in ("voz", "inicio")]
    r = admin.post(f"/admin/usuarios/{m.id}", data={"nombre": m.nombre, "rol": m.rol, "area_id": m.area_id or "",
                                                    "menus_form": "1", "menus": permitidos}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert sorted(_manuel(db).menus_bloqueados) == ["inicio", "voz"]
    assert manuel.get("/voz-cliente").status_code == 403
    assert manuel.post("/api/ia/compromiso", data={}).status_code in (200, 422)  # compromisos sigue permitido
    r = manuel.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/mi-semana"
    html = manuel.get("/mi-semana").text
    assert 'href="/voz-cliente"' not in html and 'href="/compromisos"' in html


def test_admin_no_se_bloquea(admin, db):
    a = db.scalar(select(Usuario).where(Usuario.email == "jmartinez@onecore.mx"))
    a.menus_bloqueados = ["inicio", "socio"]
    db.commit()
    assert admin.get("/").status_code == 200
    assert admin.get("/socio").status_code == 200


def test_captura_de_predictiva(manuel, db):
    p = db.scalar(select(Predictiva).join(Mci).where(Mci.clave == "ventas-base"))
    sem = semana_iso(hoy())
    r = manuel.post("/indicadores/captura", data={"semana": sem, f"pred_{p.id}": "3"}, follow_redirects=False)
    assert r.status_code == 303
    med = db.scalar(select(Medicion).where(Medicion.tipo == "predictiva", Medicion.periodo == sem))
    assert med and float(med.valor) == 3
    assert "3" in manuel.get("/?area=ventas").text


def test_ocultar_persona_del_indicador(admin, mario, db):
    m = db.scalar(select(Usuario).where(Usuario.email == "mpineda@onecore.mx"))
    assert m.nombre in admin.get("/").text.split("Indicador de cada persona")[1]
    admin.post(f"/admin/usuarios/{m.id}", data={"nombre": m.nombre, "rol": m.rol, "area_id": m.area_id or "",
                                                "menus_form": "1", "menus": ["inicio", "compromisos", "mi-semana"],
                                                "recibe_correos": "true"})
    db.refresh(m)
    assert m.en_indicador_personas is False
    assert m.nombre not in admin.get("/").text.split("Indicador de cada persona")[1]
    assert mario.get("/mi-semana").status_code == 200  # todo lo demás le sigue funcionando

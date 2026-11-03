from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AccionAgente, Informe
from app.services import ia


def test_pedir_documento_al_pm_y_no_duplicar(manuel, db, fake):
    largo = "# Plan de rescate\n\n## Acciones\n" + "\n".join(f'- Acción {i} con "comillas" y saltos' for i in range(200))
    fake.respuestas = [largo]
    d = {"tipo": "informe", "titulo": "Plan de rescate", "instrucciones": "Las acciones correspondientes",
         "compromiso_id": "", "destinatarios": []}
    r = manuel.post("/acciones/nueva", data=d, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/informes/")
    inf = db.scalar(select(Informe).where(Informe.titulo == "Plan de rescate"))
    assert inf and "Acción 199" in inf.contenido and not inf.contenido.startswith("# ")
    r2 = manuel.post("/acciones/nueva", data=d, follow_redirects=False)  # segundo clic
    assert r2.headers["location"] == r.headers["location"]
    assert db.query(AccionAgente).filter(AccionAgente.titulo == "Plan de rescate").count() == 1


def test_informe_pm_no_se_genera_dos_veces(manuel, db, fake):
    fake.respuestas = [{"resumen": "Todo bien", "acciones": [], "riesgos": []}]
    manuel.post("/pm/informe")
    manuel.post("/pm/informe")
    assert db.query(Informe).filter(Informe.tipo == "pm_diario").count() == 1


class _Resp:
    def __init__(self, texto, stop):
        self.content = [SimpleNamespace(type="text", text=texto)]
        self.stop_reason = stop
        self.usage = SimpleNamespace(input_tokens=1, output_tokens=1)


def test_respuesta_cortada_se_reintenta_y_si_no_da_mensaje_claro(monkeypatch):
    c = ia.ClienteIA()
    llamadas = []

    def crear(modelo, max_tokens, system, prompt):
        llamadas.append(max_tokens)
        return _Resp('{"titulo":"x","contenido_markdown":"texto que se cort', "max_tokens") if len(llamadas) == 1 \
            else _Resp('{"titulo":"x","contenido_markdown":"completo\nen dos líneas"}', "end_turn")
    monkeypatch.setattr(c, "_crear", crear)
    res, meta = c.pedir_json("p", max_tokens=1000)
    assert llamadas == [1000, 2000] and res["contenido_markdown"].startswith("completo")
    monkeypatch.setattr(c, "_crear", lambda *a: _Resp('{"a":"cortado', "max_tokens"))
    with pytest.raises(ValueError, match="incompleta"):
        c.pedir_json("p", max_tokens=8000)

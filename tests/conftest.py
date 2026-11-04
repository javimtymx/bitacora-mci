import os
import sys
from pathlib import Path

import pytest

DB = Path(__file__).parent / "test.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["ANTHROPIC_API_KEY"] = "fake"
os.environ["SMTP_DRY_RUN"] = "true"
os.environ["TRANSCRIPCIONES_CLAVE"] = "q2Yl8b6c1v3f0WnJ8mXo0n2pP5wqJ0k9Z1s3bQyK6aE="
os.environ["EVIDENCIAS_DIR"] = str(Path(__file__).parent / "_evidencias")
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import cli  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Usuario  # noqa: E402
from app.services import ia  # noqa: E402
from app.web.base import hash_password  # noqa: E402


class FakeIA:
    disponible = True

    def __init__(self):
        self.respuestas = []
        self.prompts = []
        self.systems = []
        self.investigaciones = []

    def pedir_json(self, prompt, rapido=False, max_tokens=1500, system=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        r = self.respuestas.pop(0) if self.respuestas else {"veredicto": "ok", "observaciones": []}
        return r, {"modelo": "fake", "tin": 10, "tout": 5}

    def pedir_json_vivo(self, prompt, max_tokens=1200, timeout=15, system=None, esquema=None):
        self.esquemas = getattr(self, "esquemas", []) + [esquema]
        self.prompts.append(prompt)
        r = self.respuestas.pop(0) if self.respuestas else {"titulo": "", "faltan": [], "mensaje": "Listo"}
        if isinstance(r, Exception):
            raise r
        return r, {"modelo": "fake-rapido", "tin": 10, "tout": 5}

    def pedir_texto(self, prompt, rapido=False, max_tokens=4000, system=None):
        self.prompts.append(prompt)
        r = self.respuestas.pop(0) if self.respuestas else "# Documento\n\nContenido"
        if isinstance(r, dict):  # compatibilidad con pruebas que daban JSON
            r = f"# {r.get('titulo', 'Documento')}\n\n{r.get('contenido_markdown', '')}"
        return r, {"modelo": "fake", "tin": 10, "tout": 5}

    def investigar(self, prompt, max_busquedas=5, system=None):
        self.investigaciones.append(prompt)
        return ({"resumen": "Seguimiento en 48 h duplica la tasa de cita.", "hallazgos": ["dato 1"], "implicacion": "retomar"},
                {"modelo": "fake", "fuentes": [{"url": "https://ejemplo.org/estudio", "titulo": "Estudio"}]})


@pytest.fixture()
def fake():
    f = FakeIA()
    ia.set_cliente(f)
    yield f
    ia.set_cliente(None)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    cli.crear_admin("jmartinez@onecore.mx", "Javier Martínez", "clave-segura-123")
    cli.seed()
    s = SessionLocal()
    for u in s.query(Usuario).all():
        u.password_hash = hash_password("clave-segura-123")
    s.commit()
    yield s
    s.close()


def login(client, email):
    r = client.post("/login", data={"email": email, "password": "clave-segura-123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return client


@pytest.fixture()
def admin(db):
    return login(TestClient(app), "jmartinez@onecore.mx")


@pytest.fixture()
def manuel(db):
    return login(TestClient(app), "mcoronado@onecore.mx")


@pytest.fixture()
def mario(db):
    return login(TestClient(app), "mpineda@onecore.mx")

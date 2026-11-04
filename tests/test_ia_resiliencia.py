from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import SesionCliente
from app.services import ia, vozcliente
from app.services.util import hoy


class _Err(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status


def _resp(texto, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class _Cli:
    def __init__(self, guion):
        self.guion, self.llamadas = list(guion), []

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.llamadas.append(kw["max_tokens"])
        x = self.guion.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def _cliente(monkeypatch, guion):
    c = ia.ClienteIA()
    cli = _Cli(guion)
    monkeypatch.setattr(c, "_anthropic", lambda *a, **k: cli)
    monkeypatch.setattr(ia.ClienteIA, "ESPERAS", (0, 0, 0))
    return c, cli


def test_reintenta_fallas_pasajeras(monkeypatch):
    c, cli = _cliente(monkeypatch, [_Err(529), _Err(503), _resp('{"ok": true}')])
    res, _ = c.pedir_json("p")
    assert res == {"ok": True} and len(cli.llamadas) == 3


def test_no_reintenta_falta_de_saldo(monkeypatch):
    e = _Err(400)
    e.args = ("Your credit balance is too low to access the Anthropic API",)
    c, cli = _cliente(monkeypatch, [e])
    with pytest.raises(ia.IANoDisponible, match="saldo"):
        c.pedir_json("p")
    assert len(cli.llamadas) == 1


def test_respuesta_cortada_crece_hasta_el_tope_y_repara(monkeypatch):
    c, cli = _cliente(monkeypatch, [_resp('{"a":"x', "max_tokens"), _resp('{"a":"x', "max_tokens"), _resp('{"a":"x', "max_tokens"),
                                    _resp('{"a":"bien"}')])
    res, _ = c.pedir_json("p", max_tokens=4000)
    assert res == {"a": "bien"} and cli.llamadas[:3] == [4000, 8000, 16000]


def test_sesion_larga_en_dos_etapas_y_en_segundo_plano(manuel, db, fake):
    base = ("Cliente: lo que más nos preocupa son las multas por pedimentos mal declarados en la aduana de Nuevo Laredo. "
            "Vendedor: con SAFE validamos el pedimento antes del pago. Cliente: el expediente lo armamos a mano cada año. "
            "Cliente: los cupos de Economía se nos vencen sin aviso. Vendedor: les ofrecemos un piloto de treinta días. ")
    tx = (base + "relleno de conversación sin contenido relevante para el análisis comercial. " * 30) * 12
    assert len(tx.split()) > vozcliente.UMBRAL_DOS_ETAPAS
    frag = {"fragmentos": [{"hablante": "cliente", "texto": "lo que más nos preocupa son las multas por pedimentos mal declarados"},
                           {"hablante": "onecore", "texto": "con SAFE validamos el pedimento antes del pago"},
                           {"hablante": "cliente", "texto": "el expediente lo armamos a mano cada año"},
                           {"hablante": "cliente", "texto": "los cupos de Economía se nos vencen sin aviso"},
                           {"hablante": "onecore", "texto": "les ofrecemos un piloto de treinta días"},
                           {"hablante": "cliente", "texto": "una frase que no existe en la transcripción"}] +
            [{"hablante": "cliente", "texto": f"relleno de conversación sin contenido relevante para el análisis comercial. relleno"}],
            "participantes": [{"nombre": "Ana Ruiz", "email": "ana@cliente.mx", "lado": "cliente"}]}
    n_tramos = len(vozcliente._tramos(tx))
    fake.respuestas = [frag] * n_tramos + [{"resumen": "ok", "valora": [], "encajes": [], "productos_interes": [],
                                            "ofertas": [], "evidencia": {"corresponde": True}}]
    r = manuel.post("/sesiones", data={"tipo": "venta_nueva", "fecha": hoy().isoformat(), "cliente_nombre": "Crown",
                                       "consentimiento": "true"},
                    files={"archivo": ("t.txt", tx.encode(), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    s = db.scalar(select(SesionCliente).where(SesionCliente.cliente_nombre == "Crown"))
    db.refresh(s)
    assert s.estado == "analizada"
    p = s.resultado["proceso"]
    assert p["modo"] == "dos_etapas" and p["tramos"] == n_tramos and p["fragmentos"] == 6  # sin la inventada ni repetidas
    assert "[Extracto" in fake.prompts[-1] and "una frase que no existe" not in fake.prompts[-1]
    assert any(x["nombre"] == "Ana Ruiz" for x in s.participantes_det)


def test_error_queda_en_la_sesion_con_mensaje(manuel, db, fake, monkeypatch):
    def falla(*a, **k):
        raise ia.IANoDisponible("La cuenta de Anthropic no tiene saldo. Agrega créditos en console.anthropic.com.")
    monkeypatch.setattr(fake, "pedir_json", falla)
    tx = "Cliente: necesitamos controlar mejor nuestros pedimentos y el expediente de cada operación. " * 20
    manuel.post("/sesiones", data={"tipo": "retencion", "fecha": hoy().isoformat(), "cliente_nombre": "Sin saldo",
                                   "consentimiento": "true"}, files={"archivo": ("t.txt", tx.encode(), "text/plain")})
    s = db.scalar(select(SesionCliente).where(SesionCliente.cliente_nombre == "Sin saldo"))
    db.refresh(s)
    assert s.estado == "error" and "saldo" in s.resultado["error"]
    t = manuel.get(f"/sesiones/{s.id}").text
    assert "Reintentar análisis" in t and "saldo" in t

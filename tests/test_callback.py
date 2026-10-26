"""Callback HTTP firmado: firma, configuración y reintentos."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from sri_txt_robot.callback import (
    CABECERA_EVENTO,
    CABECERA_FIRMA,
    CABECERA_INTENTO,
    CABECERA_TIMESTAMP,
    ConfigCallback,
    Notificador,
    firmar,
)

SECRETO = "secreto-compartido-de-prueba"
URL = "https://autotributo.test/webhooks/sri-robot"


class _Respuesta:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Transporte:
    """Registra llamadas y devuelve los códigos indicados en orden."""

    def __init__(self, *status: int | Exception) -> None:
        self.status = list(status)
        self.llamadas: list[dict[str, Any]] = []

    def __call__(self, url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> _Respuesta:
        self.llamadas.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        resultado = self.status.pop(0) if self.status else 200
        if isinstance(resultado, Exception):
            raise resultado
        return _Respuesta(resultado)


def _config(**cambios: Any) -> ConfigCallback:
    datos: dict[str, Any] = {"url": URL, "secreto": SECRETO, "timeout": 5.0, "intentos": 3, "espera_base": 0.0}
    datos.update(cambios)
    return ConfigCallback(**datos)


def test_firmar_usa_el_formato_timestamp_punto_cuerpo() -> None:
    cuerpo = b'{"status":"completed"}'
    esperado = hmac.new(SECRETO.encode(), b"1700000000." + cuerpo, hashlib.sha256).hexdigest()

    assert firmar(SECRETO, 1_700_000_000, cuerpo) == esperado
    assert len(firmar(SECRETO, 1_700_000_000, cuerpo)) == 64


def test_config_desde_entorno_desactivada_sin_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SRI_CALLBACK_URL", raising=False)
    monkeypatch.delenv("SRI_CALLBACK_SECRET", raising=False)

    assert ConfigCallback.desde_entorno() is None


def test_config_desde_entorno_exige_secreto_largo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRI_CALLBACK_URL", URL)
    monkeypatch.setenv("SRI_CALLBACK_SECRET", "corto")

    with pytest.raises(ValueError, match="al menos 16"):
        ConfigCallback.desde_entorno()


def test_config_desde_entorno_rechaza_http_publico(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRI_CALLBACK_URL", "http://autotributo.test/hook")
    monkeypatch.setenv("SRI_CALLBACK_SECRET", SECRETO)

    with pytest.raises(ValueError, match="https"):
        ConfigCallback.desde_entorno()


def test_config_acepta_la_url_del_evento_solo_si_el_host_esta_autorizado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SRI_CALLBACK_URL", "https://produccion.test/hook")
    monkeypatch.setenv("SRI_CALLBACK_SECRET", SECRETO)
    monkeypatch.setenv("SRI_CALLBACK_ALLOWED_HOSTS", "staging.test")

    autorizada = ConfigCallback.desde_entorno("https://staging.test/hook")
    rechazada = ConfigCallback.desde_entorno("https://atacante.test/hook")

    assert autorizada is not None
    assert autorizada.url == "https://staging.test/hook"
    assert rechazada is not None
    assert rechazada.url == "https://produccion.test/hook"


def test_notificador_firma_y_entrega_en_el_primer_intento() -> None:
    transporte = _Transporte(202)
    notificador = Notificador(_config(), transporte=transporte, dormir=lambda _s: None)

    entrega = notificador.enviar("evt-1", {"status": "completed"})

    assert entrega.enviado is True
    assert entrega.intentos == 1
    assert entrega.status == 202

    llamada = transporte.llamadas[0]
    cuerpo = json.loads(llamada["content"])
    timestamp = int(llamada["headers"][CABECERA_TIMESTAMP])
    assert cuerpo["timestamp"] == timestamp
    assert cuerpo["status"] == "completed"
    assert llamada["headers"][CABECERA_EVENTO] == "evt-1"
    assert llamada["headers"][CABECERA_INTENTO] == "1"
    firma = hmac.new(SECRETO.encode(), f"{timestamp}.".encode() + llamada["content"], hashlib.sha256).hexdigest()
    assert llamada["headers"][CABECERA_FIRMA] == f"sha256={firma}"


def test_notificador_reintenta_un_500_y_luego_entrega() -> None:
    transporte = _Transporte(500, 503, 200)
    esperas: list[float] = []
    notificador = Notificador(
        _config(espera_base=0.25),
        transporte=transporte,
        dormir=esperas.append,
    )

    entrega = notificador.enviar("evt-2", {"status": "failed"})

    assert entrega.enviado is True
    assert entrega.intentos == 3
    assert len(transporte.llamadas) == 3
    assert esperas == [0.25, 0.5]


def test_notificador_no_reintenta_un_400() -> None:
    transporte = _Transporte(400, 200)
    notificador = Notificador(_config(), transporte=transporte, dormir=lambda _s: None)

    entrega = notificador.enviar("evt-3", {"status": "completed"})

    assert entrega.enviado is False
    assert entrega.status == 400
    assert entrega.intentos == 1
    assert len(transporte.llamadas) == 1


def test_notificador_reintenta_errores_de_red() -> None:
    transporte = _Transporte(TimeoutError("sin red"), 202)
    notificador = Notificador(_config(), transporte=transporte, dormir=lambda _s: None)

    entrega = notificador.enviar("evt-4", {"status": "completed"})

    assert entrega.enviado is True
    assert entrega.intentos == 2
    assert entrega.error == ""


def test_notificador_respeta_el_plazo_de_la_invocacion() -> None:
    reloj = {"ahora": 100.0}
    transporte = _Transporte(500, 500, 500)
    notificador = Notificador(
        _config(espera_base=10.0),
        transporte=transporte,
        dormir=lambda segundos: reloj.update(ahora=reloj["ahora"] + segundos),
        reloj=lambda: reloj["ahora"],
    )

    entrega = notificador.enviar("evt-6", {"status": "failed"}, plazo_ms=5_000)

    assert entrega.enviado is False
    assert len(transporte.llamadas) == 1

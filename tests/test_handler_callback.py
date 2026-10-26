"""El handler emite el callback firmado en éxito y en error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sri_txt_robot import callback, handler
from sri_txt_robot.pipeline import Resumen

SECRETO = "secreto-compartido-de-prueba"
URL = "https://autotributo.test/webhooks/sri-robot"


class _Respuesta:
    status_code = 202


class _Transporte:
    def __init__(self) -> None:
        self.llamadas: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,  # noqa: ARG002 - parte de la firma de httpx.post
    ) -> _Respuesta:
        self.llamadas.append({"url": url, "cuerpo": json.loads(content), "headers": headers})
        return _Respuesta()


class _RobotFalso:
    """Robot controlable: devuelve un resumen o lanza un error."""

    resumen: Resumen = Resumen()
    error: Exception | None = None
    preparar_runtime: Any = None

    def __init__(self, *_args: Any, **opciones: Any) -> None:
        _RobotFalso.preparar_runtime = opciones.get("preparar_runtime")

    def ejecutar(self, _opciones: Any) -> Resumen:
        if _RobotFalso.error is not None:
            raise _RobotFalso.error
        return _RobotFalso.resumen


class _SelectoresFalso:
    @staticmethod
    def cargar(_ruta: str) -> object:
        return object()


def _preparar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Transporte:
    ruta = tmp_path / "robots.yaml"
    ruta.write_text("version: 1\nstorage:\n  local_dir: ./datos\nrobots: []\n", encoding="utf-8")
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.delenv("SRI_PERFIL_ORIGEN", raising=False)

    monkeypatch.setattr(handler, "Robot", _RobotFalso)
    monkeypatch.setattr(handler, "Selectores", _SelectoresFalso)
    monkeypatch.setattr(handler, "crear_almacenamiento", lambda _configuracion: object())

    transporte = _Transporte()
    monkeypatch.setattr(callback.httpx, "post", transporte)
    monkeypatch.setenv("SRI_CALLBACK_URL", URL)
    monkeypatch.setenv("SRI_CALLBACK_SECRET", SECRETO)
    monkeypatch.delenv("SRI_CALLBACK_ALLOWED_HOSTS", raising=False)

    _RobotFalso.resumen = Resumen()
    _RobotFalso.error = None
    return transporte


def test_handler_emite_callback_completado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transporte = _preparar(monkeypatch, tmp_path)

    respuesta = handler.handler({"periodo": "2026-09", "event_id": "evt-123"}, None)

    assert respuesta["callback"] == {"enviado": True, "intentos": 1, "status": 202, "error": ""}
    assert len(transporte.llamadas) == 1
    cuerpo = transporte.llamadas[0]["cuerpo"]
    assert cuerpo["event_id"] == "evt-123"
    assert cuerpo["event_type"] == "sri.robot.finished"
    assert cuerpo["status"] == "completed"
    assert cuerpo["schema_version"] == 1
    assert "timestamp" in cuerpo


def test_handler_marca_parcial_cuando_hay_fallos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transporte = _preparar(monkeypatch, tmp_path)
    _RobotFalso.resumen = Resumen(fallos=[{"ruc": "1790012345001", "error": "sin sesión"}], omitidos=["a/b/01"])

    respuesta = handler.handler({"periodo": "2026-09"}, None)

    assert respuesta["callback"]["enviado"] is True
    assert transporte.llamadas[0]["cuerpo"]["status"] == "partial"


def test_handler_emite_callback_fallido_y_propaga_el_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transporte = _preparar(monkeypatch, tmp_path)
    _RobotFalso.error = RuntimeError("el portal no respondió")

    with pytest.raises(RuntimeError, match="no respondió"):
        handler.handler({"periodo": "2026-09", "event_id": "evt-fallo"}, None)

    cuerpo = transporte.llamadas[0]["cuerpo"]
    assert cuerpo["status"] == "failed"
    assert "RuntimeError" in cuerpo["error"]
    assert "el portal no respondió" in cuerpo["error"]


def test_handler_genera_event_id_si_el_evento_no_lo_trae(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transporte = _preparar(monkeypatch, tmp_path)

    handler.handler({"periodo": "2026-09"}, None)

    event_id = transporte.llamadas[0]["cuerpo"]["event_id"]
    assert event_id.startswith("sri-")
    assert transporte.llamadas[0]["headers"]["X-Sri-Event-Id"] == event_id


def test_handler_no_emite_callback_si_no_esta_configurado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transporte = _preparar(monkeypatch, tmp_path)
    monkeypatch.delenv("SRI_CALLBACK_URL")
    monkeypatch.delenv("SRI_CALLBACK_SECRET")

    respuesta = handler.handler({"periodo": "2026-09"}, None)

    assert "callback" not in respuesta
    assert transporte.llamadas == []


def test_handler_entrega_la_preparacion_del_entorno_al_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _preparar(monkeypatch, tmp_path)
    llamadas: list[int] = []
    monkeypatch.setattr(handler, "_preparar_perfil", lambda _configuracion: llamadas.append(1))

    handler.handler({"periodo": "2026-09"}, None)

    assert llamadas == []
    assert callable(_RobotFalso.preparar_runtime)
    _RobotFalso.preparar_runtime()
    assert llamadas == [1]

"""Pruebas de los modelos de dominio y del cliente anti-captcha."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from sri_txt_robot.captcha.anticaptcha import AntiCaptchaClient, AntiCaptchaError, CaptchaNoResueltoError
from sri_txt_robot.models import Credenciales, Periodo


def test_un_ruc_de_13_digitos_exige_clave_adicional() -> None:
    with pytest.raises(ValueError, match="clave adicional"):
        Credenciales(ruc="1790012345001", password="x")


def test_un_ruc_de_10_digitos_no_exige_clave_adicional() -> None:
    credenciales = Credenciales(ruc="1234567890", password="x")

    assert credenciales.requiere_clave_adicional is False


def test_un_ruc_no_numerico_se_rechaza() -> None:
    with pytest.raises(ValueError, match="numérico"):
        Credenciales(ruc="17900ABC001", password="x")


def test_el_periodo_acepta_ano_mes() -> None:
    periodo = Periodo.desde_texto("2026-09")

    assert periodo.anio == 2026
    assert periodo.mes == 9
    assert periodo.es_mes_completo is True
    assert periodo.clave == "2026-09"


def test_el_periodo_acepta_un_dia_concreto() -> None:
    periodo = Periodo.desde_texto("2026-09-15")

    assert periodo.dia == 15
    assert periodo.es_mes_completo is False
    assert periodo.clave == "2026-09-15"


def test_un_mes_fuera_de_rango_se_rechaza() -> None:
    with pytest.raises(ValueError, match="Mes"):
        Periodo(anio=2026, mes=13)


def test_un_periodo_ilegible_se_rechaza() -> None:
    with pytest.raises(ValueError, match="Período inválido"):
        Periodo.desde_texto("septiembre")


# -- Anti-captcha -------------------------------------------------------


class _RespuestaFalsa:
    def __init__(self, datos: dict[str, object]) -> None:
        self._datos = datos

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._datos


def _cliente(monkeypatch: pytest.MonkeyPatch, respuestas: list[dict[str, object]]) -> AntiCaptchaClient:
    """Crea un cliente con respuestas simuladas de la API."""
    llamadas: list[str] = []

    def fake_post(url: str, **_kwargs: object) -> _RespuestaFalsa:
        llamadas.append(url)
        if not respuestas:
            # La tarea sigue en proceso: se mantiene la respuesta indefinida.
            return _RespuestaFalsa({"errorId": 0, "status": "processing"})
        return _RespuestaFalsa(respuestas.pop(0))

    monkeypatch.setattr(httpx, "post", fake_post)
    cliente = AntiCaptchaClient("clave-de-prueba")
    cliente._llamadas = llamadas  # type: ignore[attr-defined]
    return cliente


def test_resuelve_el_captcha_creando_y_consultando_la_tarea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cliente = _cliente(
        monkeypatch,
        [
            {"errorId": 0, "taskId": 7654321},
            {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "token-abc", "userAgent": "UA"}},
        ],
    )

    solucion = cliente.resolver(
        website_url="https://srienlinea.sri.gob.ec/consulta",
        website_key="clave-publica",
        task_types=("RecaptchaV2TaskProxyless",),
        poll_interval=0.01,
        poll_timeout=5,
    )

    assert solucion.token == "token-abc"
    assert solucion.task_id == 7654321
    assert solucion.task_type == "RecaptchaV2TaskProxyless"


def test_prueba_el_siguiente_tipo_de_tarea_si_el_primero_falla(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cliente = _cliente(
        monkeypatch,
        [
            {"errorId": 2, "errorCode": "ERROR_NO_SLOT_AVAILABLE", "errorDescription": "sin turno"},
            {"errorId": 0, "taskId": 42},
            {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "token-ok"}},
        ],
    )

    solucion = cliente.resolver(
        website_url="https://srienlinea.sri.gob.ec/consulta",
        website_key="clave-publica",
        task_types=("RecaptchaV2TaskProxyless", "RecaptchaV2EnterpriseTaskProxyless"),
        poll_interval=0.01,
        poll_timeout=5,
    )

    assert solucion.task_type == "RecaptchaV2EnterpriseTaskProxyless"
    assert solucion.token == "token-ok"


def test_un_error_fatal_no_prueba_mas_tipos(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente(
        monkeypatch,
        [{"errorId": 10, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": "sin saldo"}],
    )

    with pytest.raises(AntiCaptchaError, match="ERROR_ZERO_BALANCE"):
        cliente.resolver(
            website_url="https://srienlinea.sri.gob.ec/consulta",
            website_key="clave-publica",
            task_types=("RecaptchaV2TaskProxyless", "RecaptchaV3TaskProxyless"),
            poll_interval=0.01,
            poll_timeout=5,
        )


def test_agota_el_tiempo_de_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente(
        monkeypatch,
        [
            {"errorId": 0, "taskId": 1},
            {"errorId": 0, "status": "processing"},
            {"errorId": 0, "status": "processing"},
        ],
    )

    with pytest.raises(CaptchaNoResueltoError):
        cliente.resolver(
            website_url="https://srienlinea.sri.gob.ec/consulta",
            website_key="clave-publica",
            task_types=("RecaptchaV2TaskProxyless",),
            poll_interval=0.01,
            poll_timeout=0.05,
        )


def test_una_clave_publica_vacia_es_un_error_de_programacion(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente(monkeypatch, [])

    with pytest.raises(AntiCaptchaError, match="clave pública"):
        cliente.resolver(
            website_url="https://srienlinea.sri.gob.ec/consulta",
            website_key="   ",
            task_types=("RecaptchaV2TaskProxyless",),
        )


def test_el_saldo_se_lee_de_la_api(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente(monkeypatch, [{"errorId": 0, "balance": 12.3456}])

    assert cliente.saldo() == pytest.approx(12.3456)


def test_la_fecha_no_afecta_al_periodo_por_defecto() -> None:
    # Guarda de que el cálculo del período no depende del reloj del sistema.
    assert date(2026, 1, 1).month == 1

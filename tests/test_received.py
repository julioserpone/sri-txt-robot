"""Filtros de la consulta de comprobantes recibidos."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from playwright.sync_api import Locator

from sri_txt_robot.portal.received import ConsultaSRIError, _ajustar_ruc, _ruc_coincide

CEDULA = "1234567890"
RUC = f"{CEDULA}001"


class _CampoFalso:
    def __init__(self, actual: str, *, habilitado: bool) -> None:
        self.actual = actual
        self.habilitado = habilitado
        self.escrito: str | None = None

    def input_value(self, timeout: int = 0) -> str:
        del timeout
        return self.actual

    def is_enabled(self) -> bool:
        return self.habilitado

    def fill(self, valor: str) -> None:
        self.escrito = valor


def test_ruc_coincide_acepta_la_cedula_y_su_ruc_completo() -> None:
    assert _ruc_coincide(CEDULA, CEDULA) is True
    assert _ruc_coincide(RUC, RUC) is True
    assert _ruc_coincide(RUC, CEDULA) is True
    assert _ruc_coincide(CEDULA, RUC) is True
    assert _ruc_coincide("9999999999001", CEDULA) is False


def test_ajustar_ruc_acepta_el_campo_deshabilitado_con_el_ruc_completo() -> None:
    campo = _CampoFalso(RUC, habilitado=False)

    _ajustar_ruc(cast(Locator, campo), CEDULA)

    assert campo.escrito is None


def test_ajustar_ruc_rechaza_un_campo_deshabilitado_con_otro_ruc() -> None:
    campo = _CampoFalso("9999999999001", habilitado=False)

    with pytest.raises(ConsultaSRIError, match="deshabilitado"):
        _ajustar_ruc(cast(Locator, campo), CEDULA)


def test_ajustar_ruc_rellena_si_el_campo_esta_habilitado() -> None:
    campo = _CampoFalso("", habilitado=True)

    _ajustar_ruc(cast(Locator, campo), RUC)

    assert campo.escrito == RUC


# -- Detección del envío con token y clasificación del resultado -----------


def _respuesta(url: str, cuerpo: str, *, metodo: str = "POST") -> object:
    """Doble mínimo de ``Response`` con la petición que disparó el postback."""
    from types import SimpleNamespace

    peticion = SimpleNamespace(method=metodo, url=url, post_data=cuerpo)
    return SimpleNamespace(request=peticion)


JSF = "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/pages/consultas/recibidos/comprobantesRecibidos.jsf"


def test_solo_cuenta_el_envio_con_token_real() -> None:
    """El primer envío del portal lleva ``g-recaptcha-response`` vacío."""
    from sri_txt_robot.portal.received import _es_consulta_con_token

    sin_token = _respuesta(JSF, "frmPrincipal=principal&g-recaptcha-response=&frmPrincipal%3Aano=2026")
    con_token = _respuesta(JSF, "frmPrincipal=principal&g-recaptcha-response=03AGdBq2" + "x" * 2400)

    assert _es_consulta_con_token(cast(object, sin_token)) is False
    assert _es_consulta_con_token(cast(object, con_token)) is True


def test_no_cuenta_una_peticion_que_no_es_la_consulta() -> None:
    from sri_txt_robot.portal.received import _es_consulta_con_token

    otra = _respuesta("https://srienlinea.sri.gob.ec/otra.jsf", "g-recaptcha-response=" + "x" * 100)
    get = _respuesta(JSF, "g-recaptcha-response=" + "x" * 100, metodo="GET")

    assert _es_consulta_con_token(cast(object, otra)) is False
    assert _es_consulta_con_token(cast(object, get)) is False


def test_clasifica_la_fecha_futura_como_error_de_fecha() -> None:
    from sri_txt_robot.portal.received import _clasificar_mensaje

    assert _clasificar_mensaje("la fecha ingresada debe ser menor a la fecha actual") == "error_fecha"


def test_una_fecha_rechazada_no_reintenta_el_captcha(monkeypatch: pytest.MonkeyPatch) -> None:
    """El aviso de fecha no se confunde con un captcha rechazado."""
    from pathlib import Path

    from sri_txt_robot.portal import received

    monkeypatch.setattr(received, "primer_visible", lambda *_a, **_k: object())
    monkeypatch.setattr(received, "_esperar_resultado", lambda *_a, **_k: "error_fecha")
    llamadas: list[int] = []

    def pulsar(*_args, **_kwargs) -> str:
        llamadas.append(1)
        return "boton"

    monkeypatch.setattr(received, "_pulsar_consultar", pulsar)

    with pytest.raises(ConsultaSRIError, match="fecha"):
        received.consultar_y_descargar(
            cast(object, object()),
            _selectores_falsos(),
            Path("/tmp/sri-test-fecha"),
        )

    assert llamadas == [1], "un rechazo de fecha no debe reintentar"


def _selectores_falsos() -> object:
    """Doble de ``Selectores`` con los grupos que usa la consulta."""
    return SimpleNamespace(grupo=lambda *_a, **_k: [], valor=lambda *_a, **_k: "")


def test_tras_un_rechazo_se_vuelve_a_pulsar_consultar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada intento pide un token nuevo al widget del navegador."""
    from pathlib import Path

    from sri_txt_robot.portal import received
    from sri_txt_robot.portal.received import ResultadoDescarga

    monkeypatch.setattr(received, "primer_visible", lambda *_a, **_k: object())
    intentos: list[str] = []

    def pulsar(*_args, **_kwargs) -> str:
        intentos.append("navegador")
        return "boton"

    def resultado(*_args, **_kwargs) -> str:
        return "captcha" if len(intentos) == 1 else "ok"

    monkeypatch.setattr(received, "_pulsar_consultar", pulsar)
    monkeypatch.setattr(received, "_esperar_resultado", resultado)
    monkeypatch.setattr(received, "_descargar", lambda *_a, **_k: (b"CLAVE", "f.txt"))

    salida = received.consultar_y_descargar(
        cast(object, SimpleNamespace(url="https://srienlinea.sri.gob.ec/comprobantes")),
        _selectores_falsos(),
        Path("/tmp/sri-test-reintento"),
    )

    assert isinstance(salida, ResultadoDescarga)
    assert intentos == ["navegador", "navegador"]
    assert salida.origen_captcha == "navegador"

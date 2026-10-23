"""Filtros de la consulta de comprobantes recibidos."""

from __future__ import annotations

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

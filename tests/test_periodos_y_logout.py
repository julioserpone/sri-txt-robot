"""Pruebas de los períodos múltiples y del cierre de sesión."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sri_txt_robot import cli, handler
from sri_txt_robot.models import Periodo
from sri_txt_robot.period import sugerir_periodo
from sri_txt_robot.portal import logout
from sri_txt_robot.portal.selectors import Selectores

RAIZ = Path(__file__).resolve().parents[1]


class _Enlace:
    """Doble del enlace "Cerrar sesión"."""

    def __init__(self) -> None:
        self.clics = 0

    def click(self, **_kwargs: Any) -> None:
        self.clics += 1


class _Pagina:
    """Doble mínimo de ``Page`` para el cierre de sesión."""

    def __init__(self, url: str = "https://srienlinea.sri.gob.ec/x") -> None:
        self.url = url
        self.cerrada = False
        self.esperas: list[int] = []

    def is_closed(self) -> bool:
        return self.cerrada

    def wait_for_timeout(self, ms: int) -> None:
        self.esperas.append(ms)


def test_los_periodos_repetidos_se_respetan_en_orden() -> None:
    periodos = cli._resolver_periodos(["2026-07", "2026-08", "2026-09"])

    assert [periodo.clave for periodo in periodos] == ["2026-07", "2026-08", "2026-09"]


def test_las_listas_separadas_por_comas_tambien_sirven() -> None:
    periodos = cli._resolver_periodos(["2026-07,2026-08", "2026-09"])

    assert [periodo.clave for periodo in periodos] == ["2026-07", "2026-08", "2026-09"]


def test_los_periodos_duplicados_se_descartan() -> None:
    periodos = cli._resolver_periodos(["2026-07", "2026-07,2026-08"])

    assert [periodo.clave for periodo in periodos] == ["2026-07", "2026-08"]


def test_sin_periodos_se_usa_el_mes_cerrado_anterior() -> None:
    periodos = cli._resolver_periodos(None)
    anio, mes = sugerir_periodo(date.today())

    assert [periodo.clave for periodo in periodos] == [f"{anio:04d}-{mes:02d}"]


def test_el_evento_acepta_una_lista_de_periodos() -> None:
    periodos = handler._periodos({"periodos": ["2026-07", "2026-08"]})

    assert [periodo.clave for periodo in periodos] == ["2026-07", "2026-08"]


def test_el_evento_acepta_un_periodo_unico_por_compatibilidad() -> None:
    periodos = handler._periodos({"periodo": "2026-09"})

    assert [periodo.clave for periodo in periodos] == ["2026-09"]


def test_el_evento_acepta_una_cadena_en_lugar_de_lista() -> None:
    periodos = handler._periodos({"periodos": "2026-09"})

    assert [periodo.clave for periodo in periodos] == ["2026-09"]


def test_los_selectores_incluyen_el_cierre_de_sesion() -> None:
    selectores = Selectores.cargar(RAIZ / "config" / "selectors.yaml")

    assert selectores.grupo("sesion", "menu")
    assert any("boton-cerrar-legado" in selector for selector in selectores.grupo("sesion", "cerrar"))


def test_cerrar_sesion_despliega_el_menu_si_hace_falta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enlace = _Enlace()
    menu = _Enlace()
    llamadas: list[list[str]] = []

    def falso_primer_visible(_page: Any, selectores: list[str], **_kwargs: Any) -> _Enlace | None:
        llamadas.append(selectores)
        # Primero busca el enlace (no está), luego el menú, y tras pulsarlo
        # encuentra el enlace.
        if "menu" in selectores:
            return menu
        return enlace if menu.clics else None

    monkeypatch.setattr(logout, "primer_visible", falso_primer_visible)

    pagina = _Pagina()
    assert logout.cerrar_sesion(pagina, _selectores_de_prueba()) is True  # type: ignore[arg-type]
    assert menu.clics == 1
    assert enlace.clics == 1
    assert llamadas[1] == ["menu"]


def test_cerrar_sesion_sin_menu_devuelve_falso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout, "primer_visible", lambda *_args, **_kwargs: None)

    assert logout.cerrar_sesion(_Pagina(), _selectores_de_prueba()) is False  # type: ignore[arg-type]


def test_cerrar_sesion_no_hace_nada_si_la_pagina_esta_cerrada() -> None:
    pagina = _Pagina()
    pagina.cerrada = True

    assert logout.cerrar_sesion(pagina, _selectores_de_prueba()) is False  # type: ignore[arg-type]


def _selectores_de_prueba() -> Selectores:
    return Selectores(
        login={},
        acuerdo={},
        menu={},
        consulta={},
        captcha={},
        sesion={
            "menu": ["menu"],
            "cerrar": ["cerrar"],
            "confirmar": [],
        },
    )


@pytest.mark.parametrize("clave", ["2026-07", "2026-12", "2022-01"])
def test_un_periodo_se_puede_construir_desde_texto(clave: str) -> None:
    assert Periodo.desde_texto(clave).clave == clave

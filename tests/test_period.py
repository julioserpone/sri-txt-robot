"""Pruebas de la ventana de descarga del TXT."""

from __future__ import annotations

from datetime import date

import pytest

from sri_txt_robot.period import fin_de_periodo, periodo_anterior, sugerir_periodo, ventana_de_descarga


def test_la_ventana_se_abre_al_dia_siguiente_del_cierre() -> None:
    ventana = ventana_de_descarga(2026, 9)

    assert ventana.inicio == date(2026, 10, 1)
    assert ventana.fin == date(2026, 10, 5)


def test_la_ventana_cubre_los_cinco_dias() -> None:
    ventana = ventana_de_descarga(2026, 9)

    assert ventana.contiene(date(2026, 10, 1)) is True
    assert ventana.contiene(date(2026, 10, 5)) is True
    assert ventana.contiene(date(2026, 10, 6)) is False
    assert ventana.contiene(date(2026, 9, 30)) is False


def test_los_dias_restantes_llegan_a_cero_al_vencer() -> None:
    ventana = ventana_de_descarga(2026, 9)

    assert ventana.dias_restantes(date(2026, 10, 1)) == 4
    assert ventana.dias_restantes(date(2026, 10, 5)) == 0
    assert ventana.dias_restantes(date(2026, 10, 9)) == 0


def test_el_fin_de_periodo_responde_a_febrero() -> None:
    assert fin_de_periodo(2026, 2) == date(2026, 2, 28)
    assert fin_de_periodo(2024, 2) == date(2024, 2, 29)


def test_el_periodo_anterior_cruza_el_ano() -> None:
    assert periodo_anterior(date(2026, 1, 15)) == (2025, 12)
    assert periodo_anterior(date(2026, 10, 1)) == (2026, 9)


def test_el_periodo_sugerido_es_el_mes_anterior() -> None:
    assert sugerir_periodo(date(2026, 10, 3)) == (2026, 9)


@pytest.mark.parametrize("dia", [1, 2, 3, 4, 5])
def test_la_ventana_sigue_abierta_durante_los_primeros_cinco_dias(dia: int) -> None:
    fecha = date(2026, 10, dia)

    assert ventana_de_descarga(2026, 9).contiene(fecha) is True

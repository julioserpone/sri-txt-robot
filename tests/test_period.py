"""Pruebas del cálculo del período de emisión."""

from __future__ import annotations

from datetime import date

import pytest

from sri_txt_robot.period import (
    anios_disponibles,
    disponibilidad,
    fin_de_periodo,
    periodo_anterior,
    sugerir_periodo,
)


def test_el_fin_de_periodo_responde_a_febrero() -> None:
    assert fin_de_periodo(2026, 2) == date(2026, 2, 28)
    assert fin_de_periodo(2024, 2) == date(2024, 2, 29)


def test_el_periodo_anterior_cruza_el_ano() -> None:
    assert periodo_anterior(date(2026, 1, 15)) == (2025, 12)
    assert periodo_anterior(date(2026, 10, 1)) == (2026, 9)


def test_el_periodo_sugerido_es_el_mes_anterior() -> None:
    assert sugerir_periodo(date(2026, 10, 3)) == (2026, 9)


def test_los_anios_disponibles_son_los_cinco_del_portal() -> None:
    # Verificado en el portal en octubre de 2026: el desplegable ofrece de 2022
    # a 2026, es decir, el año en curso y los cuatro anteriores.
    assert anios_disponibles(date(2026, 10, 20)) == (2026, 2025, 2024, 2023, 2022)


def test_un_periodo_antiguo_sigue_siendo_consultable() -> None:
    # No existe la ventana de cinco días: enero de 2022 se descargó en
    # octubre de 2026 sin problema.
    estado = disponibilidad(2022, 1, date(2026, 10, 20))

    assert estado.disponible is True


def test_un_anio_fuera_del_desplegable_se_avisa() -> None:
    estado = disponibilidad(2021, 12, date(2026, 10, 20))

    assert estado.disponible is False


@pytest.mark.parametrize("mes", [1, 6, 9, 12])
def test_cualquier_mes_del_ano_en_curso_esta_disponible(mes: int) -> None:
    assert disponibilidad(2026, mes, date(2026, 10, 20)).disponible is True

"""Cálculo del período de emisión que conviene descargar.

**No existe una ventana de cinco días.** Las guías de programas contables
ecuatorianos hablan de cinco días para *importar* el reporte en el sistema
contable, pero el portal del SRI no la aplica: verificado en octubre de 2026,
se descargó septiembre de 2026 el día 20 y enero de 2022 sin problema.

El límite real es el desplegable *Periodo emisión*: ofrece los cinco años
anteriores al actual (en octubre de 2026, de 2022 a 2026). Por eso el robot no
bloquea períodos antiguos; solo elige el mes cerrado más reciente por defecto y
avisa si el año queda fuera del desplegable.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

#: Años que ofrece el desplegable del portal, contando el año en curso.
ANIOS_EN_PORTAL = 5


@dataclass(frozen=True, slots=True)
class DisponibilidadPeriodo:
    """Indica si un período puede consultarse en el portal."""

    anio: int
    mes: int
    anios_disponibles: tuple[int, ...]

    @property
    def disponible(self) -> bool:
        return self.anio in self.anios_disponibles


def fin_de_periodo(anio: int, mes: int) -> date:
    """Último día del mes indicado."""
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def periodo_anterior(hoy: date) -> tuple[int, int]:
    """Período mensual inmediatamente anterior a ``hoy``."""
    if hoy.month == 1:
        return hoy.year - 1, 12
    return hoy.year, hoy.month - 1


def sugerir_periodo(hoy: date) -> tuple[int, int]:
    """Período mensual recomendado según la fecha actual.

    Es el último mes cerrado: el que todavía no se ha procesado en una
    ejecución mensual típica. El portal permite, además, cualquier período de
    los últimos cinco años.
    """
    return periodo_anterior(hoy)


def anios_disponibles(hoy: date, *, cantidad: int = ANIOS_EN_PORTAL) -> tuple[int, ...]:
    """Años que ofrece el desplegable del portal, de más reciente a más antiguo."""
    return tuple(range(hoy.year, hoy.year - cantidad, -1))


def disponibilidad(anio: int, mes: int, hoy: date, *, cantidad: int = ANIOS_EN_PORTAL) -> DisponibilidadPeriodo:
    """Comprueba si el período cae dentro del desplegable del portal."""
    return DisponibilidadPeriodo(
        anio=anio,
        mes=mes,
        anios_disponibles=anios_disponibles(hoy, cantidad=cantidad),
    )

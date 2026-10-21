"""Cálculo de la ventana de descarga del TXT.

El SRI solo habilita la descarga del reporte TXT de comprobantes recibidos
durante los **cinco días** siguientes al cierre del período consultado. Fuera
de esa ventana el portal responde "fuera de periodo" y el reporte ya no puede
descargarse; en ese caso la alternativa es descargar los XML individuales.

Fuente: manuales de uso del portal del SRI y guías de proveedores de software
contable ecuatoriano (ZOFI, Dátil, TribuExcel), consultadas en octubre de 2026.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

#: Días hábiles/calendario que el SRI mantiene abierta la descarga del TXT.
VENTANA_DIAS = 5


@dataclass(frozen=True, slots=True)
class VentanaDescarga:
    """Ventana temporal en la que un período puede descargarse como TXT."""

    inicio: date
    fin: date

    def contiene(self, momento: date) -> bool:
        return self.inicio <= momento <= self.fin

    def dias_restantes(self, momento: date) -> int:
        """Días que faltan para el cierre de la ventana (0 si ya cerró)."""
        return max((self.fin - momento).days, 0)

    @property
    def cerrada(self) -> bool:
        return False


def fin_de_periodo(anio: int, mes: int) -> date:
    """Último día del mes indicado."""
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def ventana_de_descarga(anio: int, mes: int, *, dias: int = VENTANA_DIAS) -> VentanaDescarga:
    """Ventana de descarga del TXT para un período mensual.

    La ventana arranca al día siguiente del cierre del período y se mantiene
    abierta durante ``dias`` días.
    """
    cierre = fin_de_periodo(anio, mes)
    inicio = cierre + timedelta(days=1)
    return VentanaDescarga(inicio=inicio, fin=inicio + timedelta(days=dias - 1))


def periodo_anterior(hoy: date) -> tuple[int, int]:
    """Período mensual inmediatamente anterior a ``hoy``."""
    if hoy.month == 1:
        return hoy.year - 1, 12
    return hoy.year, hoy.month - 1


def sugerir_periodo(hoy: date) -> tuple[int, int]:
    """Período mensual recomendado según la fecha actual.

    Dentro de los primeros días del mes conviene descargar el mes cerrado
    anterior. Una vez vencido el plazo, el período anterior ya no está
    disponible como TXT y conviene avisarlo al operador.
    """
    return periodo_anterior(hoy)

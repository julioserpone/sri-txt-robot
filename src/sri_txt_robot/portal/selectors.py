"""Selectores del portal del SRI, externalizados a YAML.

El portal es una aplicación JSF/PrimeFaces cuyo HTML puede cambiar sin aviso.
Todos los selectores se declaran en ``config/selectors.yaml`` y admiten una
lista de alternativas: se usa la primera que resuelva a un elemento visible.
Así, adaptar el robot a un cambio del portal no exige tocar el código.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import Locator, Page

from ..logging import obtener_logger

logger = obtener_logger(__name__)


@dataclass(frozen=True, slots=True)
class Selectores:
    """Conjunto de selectores agrupados por pantalla."""

    login: dict[str, list[str]]
    acuerdo: dict[str, list[str]]
    menu: dict[str, list[str]]
    consulta: dict[str, list[str]]
    captcha: dict[str, list[str]]
    sesion: dict[str, list[str]]

    @classmethod
    def cargar(cls, path: str | Path) -> Selectores:
        ruta = Path(path)
        if not ruta.is_file():
            raise FileNotFoundError(f"No existe el archivo de selectores: {ruta}")

        datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
        if not isinstance(datos, dict):
            raise ValueError(f"Archivo de selectores inválido: {ruta}")

        return cls(
            login=_grupo(datos, "login"),
            acuerdo=_grupo(datos, "acuerdo"),
            menu=_grupo(datos, "menu"),
            consulta=_grupo(datos, "consulta"),
            captcha=_grupo(datos, "captcha"),
            sesion=_grupo(datos, "sesion"),
        )

    def grupo(self, pantalla: str, clave: str) -> list[str]:
        """Devuelve las alternativas de un selector concreto."""
        grupos = {
            "login": self.login,
            "acuerdo": self.acuerdo,
            "menu": self.menu,
            "consulta": self.consulta,
            "captcha": self.captcha,
            "sesion": self.sesion,
        }
        return grupos[pantalla].get(clave, [])

    def valor(self, pantalla: str, clave: str) -> str:
        """Devuelve el primer valor de un campo escalar (URL, acción, clave)."""
        alternativas = self.grupo(pantalla, clave)
        return alternativas[0].strip() if alternativas else ""


def _grupo(datos: dict[str, Any], nombre: str) -> dict[str, list[str]]:
    bruto = datos.get(nombre) or {}
    if not isinstance(bruto, dict):
        raise ValueError(f"La sección '{nombre}' de selectores debe ser un mapa.")

    grupo: dict[str, list[str]] = {}
    for clave, valor in bruto.items():
        if isinstance(valor, str):
            grupo[clave] = [valor]
        elif isinstance(valor, list):
            grupo[clave] = [str(item) for item in valor]
        else:
            raise ValueError(f"El selector '{nombre}.{clave}' debe ser texto o lista de texto.")
    return grupo


def primer_visible(page: Page, selectores: list[str], *, timeout_ms: int = 10_000) -> Locator | None:
    """Devuelve el primer selector que resuelve a un elemento visible."""
    for selector in selectores:
        try:
            localizador = page.locator(selector).first
            localizador.wait_for(state="visible", timeout=timeout_ms)
        except Exception:  # noqa: BLE001 - un selector inválido no debe abortar la cadena
            logger.debug("selector no aplicable", extra={"selector": selector})
            continue
        return localizador
    return None


def primer_visible_en_alguna_pagina(
    paginas: list[Page],
    selectores: list[str],
    *,
    timeout_ms: int = 10_000,
) -> tuple[Page, Locator] | None:
    """Busca un selector visible entre varias pestañas abiertas."""
    for pagina in paginas:
        encontrado = primer_visible(pagina, selectores, timeout_ms=timeout_ms)
        if encontrado is not None:
            return pagina, encontrado
    return None

"""Extracción de claves de acceso desde el TXT de comprobantes recibidos.

El reporte TXT del SRI es un listado de los comprobantes del período con, entre
otros datos, la **clave de acceso** de 49 dígitos de cada comprobante. El
formato exacto del archivo no está publicado por el SRI, por lo que la
extracción se basa en el patrón de la clave (49 dígitos) y no en posiciones
fijas de columnas.

Esa clave es el dato que necesita el paquete ``sri-comprobantes-php`` para
consultar el XML autorizado de cada comprobante.
"""

from __future__ import annotations

import re

from .models import TipoComprobante

#: La clave de acceso del SRI tiene exactamente 49 dígitos.
PATRON_CLAVE = re.compile(r"(?<!\d)(\d{49})(?!\d)")

#: Longitud de la base (48 dígitos) sobre la que se calcula el verificador.
LONGITUD_BASE = 48


def extraer_claves(contenido: bytes | str) -> tuple[str, ...]:
    """Extrae las claves de acceso de un TXT, sin duplicados y en orden.

    Solo se aceptan claves cuyo dígito verificador módulo 11 sea correcto, lo
    que evita arrastrar números de 49 dígitos que no son claves de acceso.
    """
    texto = contenido.decode("utf-8", errors="replace") if isinstance(contenido, bytes) else contenido

    vistos: set[str] = set()
    claves: list[str] = []

    for coincidencia in PATRON_CLAVE.finditer(texto):
        clave = coincidencia.group(1)
        if clave in vistos:
            continue
        if not verificador_valido(clave):
            continue
        vistos.add(clave)
        claves.append(clave)

    return tuple(claves)


def verificador_valido(clave: str) -> bool:
    """Comprueba el dígito verificador módulo 11 de una clave de acceso.

    Reproduce el algoritmo de la ficha técnica v2.34 (sección 5.2): pesos
    cíclicos de 2 a 7 aplicados de derecha a izquierda; 11 se traduce a 0 y
    10 a 1. Es el mismo algoritmo que implementa ``AccessKeyGenerator`` del
    paquete ``sri-comprobantes-php``.
    """
    if len(clave) != 49 or not clave.isdigit():
        return False

    base = clave[:LONGITUD_BASE]
    suma = 0
    factor = 2

    for indice in range(LONGITUD_BASE - 1, -1, -1):
        suma += int(base[indice]) * factor
        factor = 2 if factor == 7 else factor + 1

    digito = 11 - (suma % 11)
    esperado = 0 if digito == 11 else (1 if digito == 10 else digito)

    return esperado == int(clave[LONGITUD_BASE])


def nombre_txt(ruc: str, periodo: str, tipo: TipoComprobante) -> str:
    """Construye el nombre lógico del TXT para un RUC, período y tipo."""
    return f"{ruc}/{periodo}/comprobantes-recibidos-{tipo.value}.txt"


def nombre_claves(ruc: str, periodo: str, tipo: TipoComprobante) -> str:
    """Construye el nombre lógico del JSON con las claves extraídas."""
    return f"{ruc}/{periodo}/comprobantes-recibidos-{tipo.value}.claves.json"


def nombre_manifiesto(ruc: str, periodo: str) -> str:
    """Construye el nombre lógico del manifiesto del período."""
    return f"{ruc}/{periodo}/manifest.json"

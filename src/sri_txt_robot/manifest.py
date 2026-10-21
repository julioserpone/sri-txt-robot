"""Extracción de claves de acceso desde el TXT de comprobantes recibidos.

Formato real del reporte (verificado con una descarga del portal, octubre de
2026): **TSV con fila de encabezado**, sin BOM y con saltos de línea ``LF``::

    RUC_EMISOR  RAZON_SOCIAL_EMISOR  TIPO_COMPROBANTE  SERIE_COMPROBANTE
    CLAVE_ACCESO  FECHA_AUTORIZACION  FECHA_EMISION  IDENTIFICACION_RECEPTOR
    VALOR_SIN_IMPUESTOS  IVA  IMPORTE_TOTAL  NUMERO_DOCUMENTO_MODIFICADO

Son 12 columnas separadas por tabuladores; la clave de acceso de 49 dígitos es
la quinta. La extracción se hace **por nombre de columna**, no por posición, y
se valida con el dígito verificador módulo 11. Si el archivo no trae encabezado
reconocible, se recurre al patrón de 49 dígitos.

Esa clave es el dato que necesita el paquete ``sri-comprobantes-php`` para
consultar el XML autorizado de cada comprobante.
"""

from __future__ import annotations

import re

from .models import AMBIENTE_POR_DEFECTO, TipoComprobante

#: La clave de acceso del SRI tiene exactamente 49 dígitos.
PATRON_CLAVE = re.compile(r"(?<!\d)(\d{49})(?!\d)")

#: Longitud de la base (48 dígitos) sobre la que se calcula el verificador.
LONGITUD_BASE = 48

#: Nombre de la columna que contiene la clave de acceso.
COLUMNA_CLAVE = "CLAVE_ACCESO"

#: Separador de columnas del reporte.
SEPARADOR = "\t"


def extraer_claves(contenido: bytes | str) -> tuple[str, ...]:
    """Extrae las claves de acceso de un TXT, sin duplicados y en orden.

    Primero se interpreta el archivo como TSV con encabezado y se toma la
    columna ``CLAVE_ACCESO``. Si eso no produce claves, se buscan números de 49
    dígitos en todo el texto. En ambos casos solo se aceptan claves con el
    dígito verificador módulo 11 correcto, lo que evita arrastrar números que
    no son claves de acceso.
    """
    texto = contenido.decode("utf-8", errors="replace") if isinstance(contenido, bytes) else contenido

    claves: list[str] = []
    vistos: set[str] = set()

    def agregar(candidata: str) -> None:
        clave = candidata.strip()
        if clave in vistos:
            return
        if not verificador_valido(clave):
            return
        vistos.add(clave)
        claves.append(clave)

    indice = _indice_columna_clave(texto)
    if indice is not None:
        for linea in texto.splitlines()[1:]:
            if not linea.strip():
                continue
            campos = linea.split(SEPARADOR)
            if len(campos) > indice:
                agregar(campos[indice])

    if claves:
        return tuple(claves)

    for coincidencia in PATRON_CLAVE.finditer(texto):
        agregar(coincidencia.group(1))

    return tuple(claves)


def _indice_columna_clave(texto: str) -> int | None:
    """Devuelve el índice de la columna de la clave en el encabezado."""
    encabezado = texto.split("\n", 1)[0]
    if SEPARADOR not in encabezado:
        return None

    columnas = [columna.strip().upper() for columna in encabezado.split(SEPARADOR)]
    try:
        return columnas.index(COLUMNA_CLAVE)
    except ValueError:
        return None


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


def nombre_txt(ruc: str, periodo: str, tipo: TipoComprobante, *, ambiente: str = AMBIENTE_POR_DEFECTO) -> str:
    """Construye la clave del TXT.

    La jerarquía es ``ambiente/ruc/periodo/archivo`` para que el objeto se
    pueda localizar y ordenar sin conocer el código del tipo::

        produccion/1758871873001/2026-08/facturas-recibidas-2026-08.txt
    """
    return f"{_carpeta(ruc, periodo, ambiente)}/{tipo.slug}-recibidas-{periodo}.txt"


def nombre_claves(ruc: str, periodo: str, tipo: TipoComprobante, *, ambiente: str = AMBIENTE_POR_DEFECTO) -> str:
    """Construye la clave del JSON con las claves extraídas."""
    return f"{_carpeta(ruc, periodo, ambiente)}/{tipo.slug}-recibidas-{periodo}-claves.json"


def nombre_manifiesto(ruc: str, periodo: str, *, ambiente: str = AMBIENTE_POR_DEFECTO) -> str:
    """Construye la clave del manifiesto del período."""
    return f"{_carpeta(ruc, periodo, ambiente)}/manifest.json"


def _carpeta(ruc: str, periodo: str, ambiente: str) -> str:
    """Devuelve ``ambiente/ruc/periodo`` omitiendo un ambiente vacío."""
    partes = [ambiente.strip("/ "), ruc.strip("/ "), periodo.strip("/ ")]
    return "/".join(parte for parte in partes if parte)

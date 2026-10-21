"""Flujo de "Comprobantes electrónicos recibidos" y descarga del reporte TXT.

Pantalla: *Facturación Electrónica → Comprobantes electrónicos recibidos*.

Filtros: *Periodo de emisión* (año, mes y día; el día "Todos" abarca el mes
completo) y *Tipo de comprobante*. Acciones: **Consultar** (dispara la
verificación con reCAPTCHA) y **Descargar reporte** (entrega el TXT).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Download, Locator, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..captcha.anticaptcha import AntiCaptchaClient, SolucionCaptcha
from ..captcha.inject import resolver_captcha
from ..config import CaptchaConfig
from ..logging import obtener_logger
from ..models import Periodo, TipoComprobante
from .selectors import Selectores, primer_visible

logger = obtener_logger(__name__)

#: Etiquetas aceptadas para la opción "todos los días" del filtro de período.
_ETIQUETAS_TODOS = ("Todos", "TODOS", "Todos los días", "Todos los días del mes", "")

#: Tamaño máximo admitido para el TXT descargado.
_MAX_BYTES_TXT = 20 * 1024 * 1024


class ConsultaSRIError(RuntimeError):
    """La consulta al portal falló."""


@dataclass(slots=True)
class ResultadoDescarga:
    """Archivo TXT descargado y metadatos de la operación."""

    contenido: bytes
    nombre_archivo: str
    solucion: SolucionCaptcha | None = None
    url_consulta: str = ""


def navegar_a_recibidos(page: Page, selectores: Selectores) -> None:
    """Lleva el navegador a la pantalla de comprobantes recibidos.

    Usa la URL directa configurada si existe; si no, navega por el menú lateral
    ("Facturación Electrónica" → "Comprobantes electrónicos recibidos").
    """
    url_directa = (selectores.grupo("consulta", "url_directa") or [""])[0].strip()
    if url_directa:
        logger.info("navegando a la URL directa de consulta", extra={"url": url_directa})
        page.goto(url_directa, wait_until="domcontentloaded")
        _esperar_ready(page)
        return

    _aceptar_acuerdo(page, selectores)

    enlace_electronica = primer_visible(page, selectores.grupo("menu", "facturacion_electronica"))
    if enlace_electronica is not None:
        logger.info("abriendo el menú Facturación Electrónica")
        try:
            enlace_electronica.click(timeout=15_000)
        except PlaywrightTimeoutError:
            logger.debug("el menú no navegó; se continúa con el enlace directo")
    else:
        logger.warning("no se encontró el enlace 'Facturación Electrónica' en el menú")

    _esperar_ready(page)

    enlace_recibidos = primer_visible(page, selectores.grupo("menu", "comprobantes_recibidos"))
    if enlace_recibidos is None:
        raise ConsultaSRIError(
            "No se encontró el enlace 'Comprobantes electrónicos recibidos'. "
            "Configure 'consulta.url_directa' en config/selectors.yaml."
        )

    logger.info("abriendo Comprobantes electrónicos recibidos")
    with page.context.expect_page() as nueva_pagina_info:
        enlace_recibidos.click(timeout=15_000)

    try:
        nueva_pagina = nueva_pagina_info.value
        nueva_pagina.wait_for_load_state("domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        # Algunas versiones navegan en la misma pestaña.
        pass

    _esperar_ready(page)


def configurar_filtros(
    page: Page,
    selectores: Selectores,
    periodo: Periodo,
    tipo: TipoComprobante,
) -> None:
    """Fija el período de emisión y el tipo de comprobante."""
    _seleccionar(page, selectores, "anio", [str(periodo.anio)], obligatorio=True)
    _seleccionar(page, selectores, "mes", [str(periodo.mes)], obligatorio=True)

    if periodo.dia is None:
        _seleccionar(page, selectores, "dia", list(_ETIQUETAS_TODOS), obligatorio=False)
    else:
        _seleccionar(page, selectores, "dia", [str(periodo.dia)], obligatorio=True)

    _seleccionar(page, selectores, "tipo_comprobante", [tipo.value, tipo.etiqueta], obligatorio=True)


def consultar_y_descargar(
    page: Page,
    selectores: Selectores,
    cliente_captcha: AntiCaptchaClient,
    configuracion_captcha: CaptchaConfig,
    directorio: Path,
    *,
    max_intentos: int = 3,
) -> ResultadoDescarga:
    """Consulta los comprobantes y descarga el reporte TXT.

    Reintenta la resolución del captcha cuando el portal rechaza el token,
    porque cada token tiene una vigencia corta (~2 minutos).
    """
    boton_consultar = primer_visible(page, selectores.grupo("consulta", "consultar"), timeout_ms=20_000)
    if boton_consultar is None:
        raise ConsultaSRIError(
            "No se encontró el botón 'Consultar'. Revise los selectores 'consulta.consultar'."
        )

    boton_descargar = primer_visible(
        page, selectores.grupo("consulta", "descargar"), timeout_ms=20_000
    )
    if boton_descargar is None:
        raise ConsultaSRIError(
            "No se encontró el enlace 'Descargar reporte'. "
            "Revise los selectores 'consulta.descargar'."
        )

    ultimo_error: Exception | None = None

    for intento in range(1, max_intentos + 1):
        logger.info("ejecutando consulta", extra={"intento": intento, "max_intentos": max_intentos})

        solucion = resolver_captcha(page, cliente_captcha, configuracion_captcha)

        try:
            boton_consultar.click(timeout=20_000)
        except PlaywrightTimeoutError as error:
            raise ConsultaSRIError("El botón 'Consultar' no respondió.") from error

        estado = _esperar_resultados(page, selectores)

        if estado == "ok":
            contenido, nombre = _descargar(page, boton_descargar, directorio)
            return ResultadoDescarga(
                contenido=contenido,
                nombre_archivo=nombre,
                solucion=solucion,
                url_consulta=page.url,
            )

        if estado == "sin_datos":
            raise ConsultaSRIError(
                "El portal no devolvió comprobantes para el período y tipo indicados. "
                "Verifique el período (el TXT solo cubre 5 días tras el cierre) y el tipo."
            )

        # El captcha fue rechazado: se resuelve uno nuevo y se reintenta.
        ultimo_error = ConsultaSRIError("El portal rechazó el token de reCAPTCHA.")
        logger.warning("captcha rechazado, reintentando", extra={"intento": intento})
        if configuracion_captcha.report_outcomes and solucion.task_id:
            cliente_captcha.reportar_incorrecto(solucion.task_id)

    raise ultimo_error or ConsultaSRIError("No se pudo completar la consulta.")


# -- Interno ------------------------------------------------------------


def _esperar_ready(page: Page) -> None:
    """Espera a que el portal termine de pintar la pantalla."""
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        logger.debug("networkidle no alcanzado", extra={"url": page.url})


def _aceptar_acuerdo(page: Page, selectores: Selectores) -> None:
    """Acepta el aviso de protección de datos si aparece en el primer ingreso."""
    boton = primer_visible(page, selectores.grupo("acuerdo", "aceptar"), timeout_ms=3_000)
    if boton is None:
        return
    logger.info("aceptando el aviso de protección de datos")
    try:
        boton.click(timeout=10_000)
        _esperar_ready(page)
    except PlaywrightTimeoutError:
        logger.warning("no se pudo aceptar el aviso de protección de datos")


def _seleccionar(
    page: Page,
    selectores: Selectores,
    clave: str,
    opciones: list[str],
    *,
    obligatorio: bool,
) -> None:
    """Selecciona una opción en un desplegable del formulario."""
    campo = primer_visible(page, selectores.grupo("consulta", clave), timeout_ms=15_000)
    if campo is None:
        if obligatorio:
            raise ConsultaSRIError(f"No se encontró el campo '{clave}' del formulario de consulta.")
        logger.debug("campo opcional ausente", extra={"campo": clave})
        return

    for opcion in opciones:
        try:
            campo.select_option(label=opcion, timeout=5_000)
            logger.debug("opción seleccionada por etiqueta", extra={"campo": clave, "opcion": opcion})
            return
        except Exception:  # noqa: BLE001 - se intenta la siguiente alternativa
            continue

    for opcion in opciones:
        try:
            campo.select_option(value=opcion, timeout=5_000)
            logger.debug("opción seleccionada por valor", extra={"campo": clave, "opcion": opcion})
            return
        except Exception:  # noqa: BLE001
            continue

    if obligatorio:
        raise ConsultaSRIError(
            f"No se pudo seleccionar ninguna de las opciones {opciones!r} en el campo '{clave}'."
        )
    logger.warning("no se pudo seleccionar la opción del campo opcional", extra={"campo": clave})


def _esperar_resultados(page: Page, selectores: Selectores) -> str:
    """Determina si la consulta devolvió resultados, no devolvió datos o falló.

    Devuelve ``"ok"``, ``"sin_datos"`` o ``"error"``.
    """
    tabla = None
    for selector in selectores.grupo("consulta", "tabla_resultados"):
        try:
            candidato = page.locator(selector).first
            if candidato.count() > 0 and candidato.is_visible():
                tabla = candidato
                break
        except Exception:  # noqa: BLE001
            continue

    if tabla is not None:
        try:
            filas = tabla.locator("tbody tr")
            if filas.count() > 0:
                return "ok"
        except Exception:  # noqa: BLE001
            pass

    for selector in selectores.grupo("consulta", "mensaje_sin_datos"):
        try:
            elemento = page.locator(selector).first
            if elemento.count() > 0 and elemento.is_visible():
                return "sin_datos"
        except Exception:  # noqa: BLE001
            continue

    return "error"


def _descargar(page: Page, boton: Locator, directorio: Path) -> tuple[bytes, str]:
    """Pulsa 'Descargar reporte' y devuelve el contenido del TXT."""
    directorio.mkdir(parents=True, exist_ok=True)

    try:
        with page.expect_download(timeout=60_000) as info:
            boton.click(timeout=20_000)
        descarga: Download = info.value
        ruta = directorio / descarga.suggested_filename
        descarga.save_as(str(ruta))
    except PlaywrightTimeoutError as error:
        raise ConsultaSRIError(
            "No se inició la descarga del reporte TXT (tiempo de espera agotado)."
        ) from error

    contenido = ruta.read_bytes()
    if len(contenido) > _MAX_BYTES_TXT:
        raise ConsultaSRIError("El TXT descargado supera el tamaño máximo admitido.")
    if not contenido.strip():
        raise ConsultaSRIError("El TXT descargado está vacío.")

    return contenido, ruta.name

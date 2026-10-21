"""Flujo de "Comprobantes electrónicos recibidos" y descarga del reporte TXT.

Pantalla: *Facturación Electrónica → Comprobantes electrónicos recibidos*
(``/comprobantes-electronicos-internet/pages/consultas/recibidos/comprobantesRecibidos.jsf``).

Filtros: *Periodo emisión* (año, mes y día; el día "Todos" abarca el mes
completo) y *Tipo de comprobante*. Acciones: **Consultar** (dispara el
reCAPTCHA) y **Descargar reporte** (entrega el TXT).

Estrategia del captcha
----------------------

1. Se pulsa "Consultar" y se deja que el propio navegador resuelva el
   reCAPTCHA invisible. Es el flujo real del portal y no tiene coste.
2. Si el portal rechaza el token, se resuelve con anti-captcha, se inyecta y se
   envía la consulta con ``rcBuscar()``.

La espera no es un ``sleep``: se escucha la respuesta de la consulta y solo se
considera enviada cuando la petición lleva un ``g-recaptcha-response`` no
vacío. El primer envío del portal (sin token) se ignora porque el propio SRI lo
rechaza.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Download, Locator, Page, Request, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..captcha.anticaptcha import AntiCaptchaClient, SolucionCaptcha
from ..captcha.inject import enviar_consulta, resolver_captcha
from ..config import CaptchaConfig
from ..logging import obtener_logger
from ..models import Periodo, TipoComprobante
from .selectors import Selectores, primer_visible

logger = obtener_logger(__name__)

#: Etiquetas aceptadas para la opción "todos los días" del filtro de período.
_ETIQUETAS_TODOS = ("Todos", "TODOS", "Todos los días", "Todos los días del mes", "0", "")

#: Tamaño máximo admitido para el TXT descargado.
_MAX_BYTES_TXT = 20 * 1024 * 1024

#: Textos con los que el portal informa cada desenlace de la consulta.
_TEXTO_SIN_DATOS = ("no existen datos", "no hay datos", "no se encontraron")
_TEXTO_CAPTCHA = "captcha"

#: Detecta un envío de consulta que lleva token de reCAPTCHA.
_PATRON_TOKEN_ENVIO = re.compile(r"g-recaptcha-response(?:-\d+)?=(?!&)([^&]{20,})")

#: Segundos que se espera la respuesta de una consulta válida.
_ESPERA_RESPUESTA_MS = 30_000


class ConsultaSRIError(RuntimeError):
    """La consulta al portal falló."""


class SinDatosError(ConsultaSRIError):
    """El período y tipo consultados no tienen comprobantes.

    No es un fallo: un contribuyente puede no recibir, por ejemplo, notas de
    débito en un mes. El pipeline lo registra como omitido.
    """


@dataclass(slots=True)
class ResultadoDescarga:
    """Archivo TXT descargado y metadatos de la operación."""

    contenido: bytes
    nombre_archivo: str
    solucion: SolucionCaptcha | None = None
    url_consulta: str = ""
    origen_captcha: str = ""


def navegar_a_recibidos(page: Page, selectores: Selectores) -> None:
    """Lleva el navegador a la pantalla de comprobantes recibidos.

    Usa la URL directa configurada si existe; si no, el enlace del portal que
    entrega el token de acceso a la aplicación JSF.

    Se navega siempre, aunque ya se esté en la pantalla: el portal renderiza un
    widget de reCAPTCHA por carga y conviene partir de un estado limpio para
    cada tipo de comprobante.
    """
    url_directa = selectores.valor("consulta", "url_directa")
    if url_directa:
        logger.info("navegando a la pantalla de consulta", extra={"url": url_directa})
        page.goto(url_directa, wait_until="domcontentloaded")
        _esperar_formulario(page, selectores)
        return

    _aceptar_acuerdo(page, selectores)

    enlace_recibidos = primer_visible(page, selectores.grupo("menu", "comprobantes_recibidos"), timeout_ms=20_000)
    if enlace_recibidos is None:
        raise ConsultaSRIError(
            "No se encontró el enlace 'Comprobantes electrónicos recibidos'. "
            "Configure 'consulta.url_directa' en config/selectors.yaml."
        )

    logger.info("abriendo Comprobantes electrónicos recibidos desde el menú")
    enlace_recibidos.click(timeout=20_000)
    _esperar_formulario(page, selectores)


def configurar_filtros(
    page: Page,
    selectores: Selectores,
    periodo: Periodo,
    tipo: TipoComprobante,
    *,
    ruc: str = "",
) -> None:
    """Fija el RUC, el período de emisión y el tipo de comprobante."""
    if ruc:
        campo_parametro = primer_visible(page, selectores.grupo("consulta", "parametro"), timeout_ms=10_000)
        if campo_parametro is not None:
            _ajustar_ruc(campo_parametro, ruc)

    _seleccionar(page, selectores, "anio", [str(periodo.anio)], obligatorio=True)
    _seleccionar(page, selectores, "mes", [str(periodo.mes)], obligatorio=True)

    if periodo.dia is None:
        _seleccionar(page, selectores, "dia", list(_ETIQUETAS_TODOS), obligatorio=False)
    else:
        _seleccionar(page, selectores, "dia", [str(periodo.dia)], obligatorio=True)

    _seleccionar(
        page,
        selectores,
        "tipo_comprobante",
        [tipo.valor_portal, tipo.etiqueta],
        obligatorio=True,
    )


def _ajustar_ruc(campo: Locator, ruc: str) -> None:
    """Ajusta el RUC del filtro de consulta.

    El portal deja el campo *Ruc/Cédula/Pasaporte* **deshabilitado** con el RUC
    de la sesión; escribir en él hace esperar a Playwright hasta agotar el
    tiempo. Solo se rellena si está habilitado y su valor no coincide.
    """
    try:
        actual = (campo.input_value(timeout=5_000) or "").strip()
    except PlaywrightTimeoutError:
        actual = ""

    if actual == ruc:
        return
    if campo.is_enabled():
        campo.fill(ruc)
        return

    raise ConsultaSRIError(f"El filtro de RUC está deshabilitado con '{actual}' y se pidió '{ruc}'.")


def consultar_y_descargar(
    page: Page,
    selectores: Selectores,
    cliente_captcha: AntiCaptchaClient | None,
    configuracion_captcha: CaptchaConfig,
    directorio: Path,
    *,
    max_intentos: int = 3,
) -> ResultadoDescarga:
    """Consulta los comprobantes y descarga el reporte TXT.

    Primero se deja que el navegador resuelva el captcha. Si el portal lo
    rechaza y hay cliente de anti-captcha, se resuelve con el servicio y se
    reintenta. Cada token dura unos dos minutos, así que se resuelve justo
    antes de enviar.
    """
    if primer_visible(page, selectores.grupo("consulta", "consultar"), timeout_ms=20_000) is None:
        raise ConsultaSRIError("No se encontró el botón 'Consultar'. Revise los selectores 'consulta.consultar'.")

    ultimo_error: Exception | None = None
    intentar_en_navegador = configuracion_captcha.intentar_en_navegador

    for intento in range(1, max_intentos + 1):
        logger.info("ejecutando consulta", extra={"intento": intento, "max_intentos": max_intentos})

        solucion: SolucionCaptcha | None = None
        origen = "navegador"

        if intentar_en_navegador:
            mecanismo = _pulsar_consultar(page, selectores)
            if mecanismo:
                logger.info("consulta enviada por el flujo del portal", extra={"mecanismo": mecanismo})
        else:
            if cliente_captcha is None:
                raise ConsultaSRIError(
                    "El portal rechazó el captcha del navegador y no hay clave de "
                    "anti-captcha configurada. Defina ANTICAPTCHA_API_KEY o use un "
                    "navegador con sesión real."
                )
            solucion = resolver_captcha(
                page,
                cliente_captcha,
                configuracion_captcha,
                sitekey_respaldo=selectores.valor("captcha", "sitekey"),
                accion_respaldo=selectores.valor("captcha", "accion"),
            )
            enviar_consulta(page)
            origen = solucion.task_type

        estado = _esperar_resultado(page, selectores)

        if estado == "ok":
            contenido, nombre = _descargar(page, selectores, directorio)
            if solucion is not None and cliente_captcha is not None and configuracion_captcha.report_outcomes:
                cliente_captcha.reportar_correcto(solucion.task_id)
            return ResultadoDescarga(
                contenido=contenido,
                nombre_archivo=nombre,
                solucion=solucion,
                url_consulta=page.url,
                origen_captcha=origen,
            )

        if estado == "sin_datos":
            raise SinDatosError("El portal no tiene comprobantes para el período y tipo indicados.")

        if estado == "error_formulario":
            raise ConsultaSRIError(
                "El portal rechazó la consulta por los datos del formulario "
                "(RUC, período o tipo). Revise la configuración."
            )

        if estado != "captcha":
            raise ConsultaSRIError("El portal no devolvió resultados ni un aviso reconocible.")

        # El captcha fue rechazado: se resuelve uno nuevo y se reintenta.
        ultimo_error = ConsultaSRIError("El portal rechazó el token de reCAPTCHA.")
        logger.warning("captcha rechazado, reintentando", extra={"intento": intento})

        if solucion is not None and cliente_captcha is not None and configuracion_captcha.report_outcomes:
            cliente_captcha.reportar_incorrecto(solucion.task_id)

        # Tras un rechazo, se pasa a anti-captcha aunque el primer intento haya
        # sido del navegador.
        intentar_en_navegador = False

    raise ultimo_error or ConsultaSRIError("No se pudo completar la consulta.")


# -- Interno ------------------------------------------------------------


def _pulsar_consultar(page: Page, selectores: Selectores) -> str:
    """Pulsa "Consultar" y espera la respuesta que lleva token de reCAPTCHA."""
    boton = primer_visible(page, selectores.grupo("consulta", "consultar"), timeout_ms=10_000)
    if boton is None:
        return ""
    with _CapturarConsultasValidas(page) as capturadas:
        try:
            boton.click(timeout=20_000)
        except PlaywrightTimeoutError as error:
            raise ConsultaSRIError("El botón 'Consultar' no respondió.") from error
        _esperar_a(lambda: bool(capturadas), _ESPERA_RESPUESTA_MS)
    return "boton" if capturadas else ""


def _esperar_resultado(page: Page, selectores: Selectores) -> str:
    """Clasifica el desenlace de la consulta leyendo la pantalla.

    Devuelve ``"ok"``, ``"sin_datos"``, ``"captcha"``, ``"error_formulario"`` o
    ``"error"``.
    """
    # La respuesta ya llegó; se deja que PrimeFaces pinte el panel.
    page.wait_for_timeout(1_200)

    if _tabla_con_filas(page, selectores):
        return "ok"

    texto = _texto_mensajes(page, selectores)

    if any(fragmento in texto for fragmento in _TEXTO_SIN_DATOS):
        return "sin_datos"

    if any(fragmento in texto for fragmento in _TEXTO_CAPTCHA):
        return "captcha"

    if texto:
        logger.warning("aviso no reconocido del portal", extra={"texto": texto[:300]})
        return "error_formulario" if "ruc" in texto or "parámetro" in texto else "error"

    return "error"


def _tabla_con_filas(page: Page, selectores: Selectores) -> bool:
    for selector in selectores.grupo("consulta", "tabla_resultados"):
        try:
            tabla = page.locator(selector).first
            if tabla.count() > 0 and tabla.is_visible() and tabla.locator("tbody tr").count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def texto_mensajes(page: Page, selectores: Selectores) -> str:
    """Devuelve los avisos del portal en minúsculas (texto plano)."""
    return _texto_mensajes(page, selectores)


def _texto_mensajes(page: Page, selectores: Selectores) -> str:
    fragmentos: list[str] = []
    for selector in selectores.grupo("consulta", "mensajes"):
        try:
            elemento = page.locator(selector).first
            if elemento.count() > 0:
                fragmentos.append(elemento.inner_text())
        except Exception:  # noqa: BLE001
            continue
    return " ".join(fragmentos).strip().lower()


def _es_consulta_con_token(request: Request) -> bool:
    """Indica si la petición es una consulta que lleva token de reCAPTCHA."""
    if request.method != "POST" or "comprobantesRecibidos.jsf" not in request.url:
        return False
    try:
        datos = request.post_data or ""
    except Exception:  # noqa: BLE001
        return False
    return bool(_PATRON_TOKEN_ENVIO.search(datos))


class _CapturarConsultasValidas:
    """Contexto que acumula las respuestas de consultas con token."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self.respuestas: list[Response] = []

    def __enter__(self) -> list[Response]:
        self._page.on("response", self._al_recibir)
        return self.respuestas

    def __exit__(self, *_excepcion: object) -> None:
        try:
            self._page.remove_listener("response", self._al_recibir)
        except Exception:  # noqa: BLE001
            logger.debug("no se pudo quitar el escucha de respuestas", exc_info=True)

    def _al_recibir(self, respuesta: Response) -> None:
        if _es_consulta_con_token(respuesta.request):
            self.respuestas.append(respuesta)


def _esperar_a(condicion: Callable[[], bool], timeout_ms: int) -> bool:
    """Espera a que una condición sea verdadera, sondeando cada 250 ms."""
    limite = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.25)
    return False


def _esperar_formulario(page: Page, selectores: Selectores) -> None:
    """Espera a que la pantalla de consulta esté lista."""
    campo = primer_visible(page, selectores.grupo("consulta", "anio"), timeout_ms=45_000)
    if campo is None:
        raise ConsultaSRIError(
            "No se cargó la pantalla de comprobantes recibidos "
            "(no aparece el filtro de año). Puede que la sesión haya caducado."
        )
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
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
            campo.select_option(value=opcion, timeout=5_000)
            logger.debug("opción seleccionada por valor", extra={"campo": clave, "opcion": opcion})
            return
        except Exception:  # noqa: BLE001 - se intenta la siguiente alternativa
            continue

    for opcion in opciones:
        try:
            campo.select_option(label=opcion, timeout=5_000)
            logger.debug("opción seleccionada por etiqueta", extra={"campo": clave, "opcion": opcion})
            return
        except Exception:  # noqa: BLE001
            continue

    if obligatorio:
        raise ConsultaSRIError(f"No se pudo seleccionar ninguna de las opciones {opciones!r} en el campo '{clave}'.")
    logger.warning("no se pudo seleccionar la opción del campo opcional", extra={"campo": clave})


def _descargar(page: Page, selectores: Selectores, directorio: Path) -> tuple[bytes, str]:
    """Pulsa 'Descargar reporte' y devuelve el contenido del TXT."""
    enlace = primer_visible(page, selectores.grupo("consulta", "descargar"), timeout_ms=20_000)
    if enlace is None:
        raise ConsultaSRIError(
            "No se encontró el enlace 'Descargar reporte'. Revise los selectores 'consulta.descargar'."
        )

    directorio.mkdir(parents=True, exist_ok=True)

    try:
        with page.expect_download(timeout=90_000) as info:
            enlace.click(timeout=20_000)
        descarga: Download = info.value
        ruta = directorio / descarga.suggested_filename
        descarga.save_as(str(ruta))
    except PlaywrightTimeoutError as error:
        raise ConsultaSRIError("No se inició la descarga del reporte TXT (tiempo de espera agotado).") from error

    contenido = ruta.read_bytes()
    if len(contenido) > _MAX_BYTES_TXT:
        raise ConsultaSRIError("El TXT descargado supera el tamaño máximo admitido.")
    if not contenido.strip():
        raise ConsultaSRIError("El TXT descargado está vacío.")

    return contenido, ruta.name

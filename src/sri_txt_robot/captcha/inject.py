"""Resolución e inyección del reCAPTCHA en la página del portal.

El portal del SRI protege la consulta de comprobantes con reCAPTCHA. La clave
pública no está fija en el HTML de la aplicación JSF, por lo que se detecta en
tiempo de ejecución; en el portal nuevo además se obtiene dinámicamente desde
``/sri-captcha-servicio-internet/rest/ValidacionCaptcha/obtenerClavePublicaGoogleRecaptcha``.

Una vez resuelto, el token se inyecta en el formulario de dos formas
complementarias:

1. Se escribe en el campo ``g-recaptcha-response`` (lo que lee el servidor).
2. Se sustituye ``grecaptcha.getResponse()`` para que el JavaScript de la
   página devuelva el token en lugar de una cadena vacía.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

from ..config import CaptchaConfig
from ..logging import obtener_logger
from .anticaptcha import AntiCaptchaClient, SolucionCaptcha

logger = obtener_logger(__name__)

#: Longitudes típicas de una clave pública de reCAPTCHA.
_LONGITUD_MINIMA = 20
_LONGITUD_MAXIMA = 120

_PATRON_SITEKEY = re.compile(r"(?:sitekey|siteKey)['\"]?\s*[:=]\s*['\"]([0-9A-Za-z_-]{20,120})['\"]")


def detectar_sitekey(page: Page) -> str | None:
    """Localiza la clave pública del reCAPTCHA en la página actual."""
    try:
        elemento = page.locator("[data-sitekey]").first
        if elemento.count() > 0:
            clave = elemento.get_attribute("data-sitekey")
            if clave and len(clave) >= _LONGITUD_MINIMA:
                logger.info("clave pública localizada en data-sitekey")
                return clave
    except Exception:  # noqa: BLE001
        logger.debug("no se pudo leer data-sitekey", exc_info=True)

    try:
        contenido = page.evaluate("() => Array.from(document.scripts).map(s => s.textContent || '').join('\\n')")
    except Exception:  # noqa: BLE001
        logger.debug("no se pudieron leer los scripts de la página", exc_info=True)
        return None

    coincidencia = _PATRON_SITEKEY.search(contenido)
    if coincidencia:
        logger.info("clave pública localizada en el script de la página")
        return coincidencia.group(1)

    logger.warning("no se pudo localizar la clave pública del reCAPTCHA")
    return None


def es_invisible(page: Page) -> bool:
    """Indica si el widget de reCAPTCHA es invisible."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const c = document.querySelector('.g-recaptcha, [data-sitekey]');
                    if (!c) return true;
                    const tam = c.getAttribute('data-size') || '';
                    return tam.toLowerCase() === 'invisible';
                }"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def es_enterprise(page: Page) -> bool:
    """Indica si la página usa reCAPTCHA Enterprise."""
    try:
        return bool(
            page.evaluate(
                """() => !!(window.grecaptcha && window.grecaptcha.enterprise)
                    || !!document.querySelector('script[src*="recaptcha/enterprise"]')"""
            )
        )
    except Exception:  # noqa: BLE001
        return False


def inyectar_token(page: Page, token: str) -> bool:
    """Escribe el token en el formulario y neutraliza la validación del widget.

    Devuelve ``True`` si se encontró el campo ``g-recaptcha-response``.
    """
    return bool(
        page.evaluate(
            """(token) => {
                const selectores = [
                    'textarea[name="g-recaptcha-response"]',
                    'input[name="g-recaptcha-response"]',
                    '[name="g-recaptcha-response"]'
                ];
                let encontrado = false;
                for (const selector of selectores) {
                    const nodos = document.querySelectorAll(selector);
                    for (const nodo of nodos) {
                        nodo.value = token;
                        nodo.removeAttribute('disabled');
                        nodo.dispatchEvent(new Event('input', { bubbles: true }));
                        nodo.dispatchEvent(new Event('change', { bubbles: true }));
                        encontrado = true;
                    }
                }

                // Si la página no renderizó el campo (por ejemplo, porque el
                // widget se carga por JavaScript), se crea uno oculto.
                if (!encontrado) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'g-recaptcha-response';
                    input.value = token;
                    (document.querySelector('form') || document.body).appendChild(input);
                    encontrado = true;
                }

                // El widget de Google mantiene su propio token interno y lo
                // reinicia al expirar. Se sustituyen ambos accesos para que
                // la página lea siempre el token resuelto.
                const aplicar = (objeto) => {
                    if (!objeto) return;
                    try { objeto.getResponse = () => token; } catch (e) { /* noop */ }
                    try {
                        if (typeof objeto.reset === 'function') { objeto.reset = () => {}; }
                    } catch (e) { /* noop */ }
                    try {
                        if (typeof objeto.execute === 'function') {
                            objeto.execute = () => Promise.resolve(token);
                        }
                    } catch (e) { /* noop */ }
                };
                aplicar(window.grecaptcha);
                if (window.grecaptcha && window.grecaptcha.enterprise) {
                    aplicar(window.grecaptcha.enterprise);
                }
                return encontrado;
            }""",
            token,
        )
    )


def resolver_captcha(
    page: Page,
    cliente: AntiCaptchaClient,
    configuracion: CaptchaConfig,
) -> SolucionCaptcha:
    """Resuelve el reCAPTCHA de la página actual y devuelve el token."""
    sitekey = detectar_sitekey(page)
    if sitekey is None:
        raise RuntimeError("No se encontró la clave pública del reCAPTCHA en la página de consulta.")

    enterprise = es_enterprise(page)
    invisible = es_invisible(page)

    tipos = list(configuracion.task_types)
    if enterprise:
        # Se priorizan los tipos Enterprise si la página los usa.
        tipos.sort(key=lambda t: 0 if "Enterprise" in t else 1)
    else:
        tipos.sort(key=lambda t: 0 if "Enterprise" not in t else 1)

    logger.info(
        "resolviendo reCAPTCHA",
        extra={"enterprise": enterprise, "invisible": invisible, "tipos": tipos},
    )

    solucion = cliente.resolver(
        website_url=page.url,
        website_key=sitekey,
        task_types=tuple(tipos),
        poll_interval=configuracion.poll_interval_seconds,
        poll_timeout=configuracion.poll_timeout_seconds,
        is_invisible=invisible or None,
    )

    inyectar_token(page, solucion.token)
    logger.info(
        "token de reCAPTCHA inyectado",
        extra={"task_type": solucion.task_type, "task_id": solucion.task_id, "coste": solucion.coste},
    )
    return solucion

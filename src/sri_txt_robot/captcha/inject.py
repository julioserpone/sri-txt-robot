"""Resolución e inyección del reCAPTCHA en la pantalla de consulta.

Hallazgos verificados contra el portal real (octubre de 2026):

* El widget es **reCAPTCHA Enterprise invisible**. El portal lo renderiza con
  ``cargarRecaptcha('recaptcha-container', '<sitekey>', 'SI')`` y lo dispara
  con ``executeRecaptcha('consulta_cel_recibidos', 'SI')``.
* El ``onclick`` de "Consultar" hace dos cosas: ejecuta el captcha y, en
  paralelo, un envío **sin token** que el portal rechaza con "Captcha
  incorrecta". El envío bueno lo hace el callback del widget, que llama a
  ``rcBuscar()``.
* El servidor valida el token con el endpoint de reCAPTCHA **v2**: un token v3
  generado con ``grecaptcha.enterprise.execute({action})`` se rechaza con "No
  se pudo validar el captcha en google". Por eso la tarea de anti-captcha debe
  ser ``RecaptchaV2EnterpriseTaskProxyless``.

Flujo del robot: obtener el token, escribirlo en los campos
``g-recaptcha-response*``, neutralizar ``grecaptcha`` para que la página no lo
sobrescriba y llamar a ``rcBuscar()``, que es exactamente lo que hace el
portal cuando el captcha se resuelve.
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

#: ``enterprise.js?render=<clave>``: la forma en que el portal declara la clave.
_PATRON_RENDER = re.compile(r"recaptcha/enterprise\.js\?render=([0-9A-Za-z_-]{20,120})")

#: ``cargarRecaptcha('<contenedor>', '<clave>', 'SI')``.
_PATRON_CARGAR = re.compile(r"cargarRecaptcha\(\s*[^,]+,\s*['\"]([0-9A-Za-z_-]{20,120})['\"]")

#: ``grecaptcha.render(..., {sitekey: '<clave>'})`` o ``data-sitekey``.
_PATRON_SITEKEY = re.compile(r"(?:data-sitekey\s*=\s*|sitekey\s*:\s*)['\"]([0-9A-Za-z_-]{20,120})['\"]")

#: ``executeRecaptcha('<acción>', 'SI')``.
_PATRON_ACCION = re.compile(r"executeRecaptcha\(\s*['\"]([0-9A-Za-z_]{2,80})['\"]")

#: Nombre del campo donde el portal espera el token.
_CAMPO_TOKEN = 'textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"], [id^="g-recaptcha-response"]'


def detectar_sitekey(page: Page) -> str | None:
    """Localiza la clave pública del reCAPTCHA en la página actual."""
    try:
        elemento = page.locator("[data-sitekey]").first
        if elemento.count() > 0:
            clave = elemento.get_attribute("data-sitekey")
            if clave and _LONGITUD_MINIMA <= len(clave) <= _LONGITUD_MAXIMA:
                logger.info("clave pública localizada en data-sitekey")
                return clave
    except Exception:  # noqa: BLE001
        logger.debug("no se pudo leer data-sitekey", exc_info=True)

    try:
        html = page.content()
    except Exception:  # noqa: BLE001
        logger.debug("no se pudo leer el HTML de la página", exc_info=True)
        return None

    for patron, origen in (
        (_PATRON_RENDER, "enterprise.js?render"),
        (_PATRON_CARGAR, "cargarRecaptcha"),
        (_PATRON_SITEKEY, "sitekey"),
    ):
        coincidencia = patron.search(html)
        if coincidencia:
            logger.info("clave pública localizada", extra={"origen": origen})
            return coincidencia.group(1)

    logger.warning("no se pudo localizar la clave pública del reCAPTCHA")
    return None


def detectar_accion(page: Page) -> str | None:
    """Devuelve la acción con la que el portal ejecuta el reCAPTCHA."""
    try:
        html = page.content()
    except Exception:  # noqa: BLE001
        return None

    coincidencia = _PATRON_ACCION.search(html)
    if coincidencia:
        logger.info("acción del reCAPTCHA detectada", extra={"accion": coincidencia.group(1)})
        return coincidencia.group(1)

    return None


def es_invisible(page: Page) -> bool:
    """Indica si el widget de reCAPTCHA es invisible."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const html = document.documentElement.innerHTML;
                    if (/size\\s*:\\s*['"]invisible['"]/.test(html)) return true;
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

    Devuelve ``True`` si se encontró al menos un campo ``g-recaptcha-response``.
    """
    return bool(page.evaluate(_JS_INYECTAR, token))


def enviar_consulta(page: Page) -> str:
    """Envía la consulta como lo hace el portal tras resolver el captcha.

    Devuelve el mecanismo usado: ``rcBuscar``, ``boton`` o una cadena vacía si
    no se encontró ninguno.
    """
    mecanismo = str(page.evaluate(_JS_ENVIAR))
    if mecanismo:
        logger.info("consulta enviada", extra={"mecanismo": mecanismo})
    else:
        logger.warning("no se pudo enviar la consulta: falta rcBuscar y el botón")
    return mecanismo


def resolver_captcha(
    page: Page,
    cliente: AntiCaptchaClient,
    configuracion: CaptchaConfig,
    *,
    sitekey_respaldo: str = "",
    accion_respaldo: str = "",
) -> SolucionCaptcha:
    """Resuelve el reCAPTCHA de la página actual y devuelve el token."""
    sitekey = detectar_sitekey(page) or sitekey_respaldo
    if not sitekey:
        raise RuntimeError("No se encontró la clave pública del reCAPTCHA en la página de consulta.")

    accion = detectar_accion(page) or accion_respaldo
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
        extra={
            "enterprise": enterprise,
            "invisible": invisible,
            "tipos": tipos,
            "accion": accion,
        },
    )

    solucion = cliente.resolver(
        website_url=page.url,
        website_key=sitekey,
        task_types=tuple(tipos),
        poll_interval=configuracion.poll_interval_seconds,
        poll_timeout=configuracion.poll_timeout_seconds,
        is_invisible=invisible or None,
        page_action=accion or None,
    )

    inyectar_token(page, solucion.token)
    logger.info(
        "token de reCAPTCHA inyectado",
        extra={"task_type": solucion.task_type, "task_id": solucion.task_id, "coste": solucion.coste},
    )
    return solucion


# -- JavaScript ---------------------------------------------------------

_JS_INYECTAR = """(token) => {
        const nodos = document.querySelectorAll('__CAMPO_TOKEN__');
        let encontrado = false;
        for (const nodo of nodos) {
            nodo.value = token;
            nodo.removeAttribute('disabled');
            nodo.dispatchEvent(new Event('input', { bubbles: true }));
            nodo.dispatchEvent(new Event('change', { bubbles: true }));
            encontrado = true;
        }

        // Si la página no renderizó el campo, se crea uno oculto.
        if (!encontrado) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'g-recaptcha-response';
            input.value = token;
            (document.querySelector('form') || document.body).appendChild(input);
            encontrado = true;
        }

        // El widget de Google mantiene su propio token y lo reinicia al
        // expirar. Se sustituyen sus funciones para que la página no lo
        // sobrescriba con una cadena vacía.
        const fijar = (objeto) => {
            if (!objeto) return;
            try { objeto.getResponse = () => token; } catch (e) { /* noop */ }
            try { objeto.reset = () => {}; } catch (e) { /* noop */ }
            try { objeto.execute = () => Promise.resolve(token); } catch (e) { /* noop */ }
            try { objeto.ready = (cb) => { try { cb(); } catch (e) { /* noop */ } }; } catch (e) { /* noop */ }
        };
        fijar(window.grecaptcha);
        if (window.grecaptcha && window.grecaptcha.enterprise) {
            fijar(window.grecaptcha.enterprise);
        }
        return encontrado;
    }""".replace("__CAMPO_TOKEN__", _CAMPO_TOKEN)

_JS_ENVIAR = """() => {
    if (typeof window.rcBuscar === 'function') {
        window.rcBuscar();
        return 'rcBuscar';
    }
    const boton = document.getElementById('frmPrincipal:btnBuscar');
    if (boton) {
        boton.click();
        return 'boton';
    }
    return '';
}"""

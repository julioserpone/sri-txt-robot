"""Inicio de sesión en el portal del SRI (Keycloak).

El formulario de acceso vive en ``/tuportal-internet/`` y lo publica Keycloak
con la acción ``/auth/realms/Internet/login-actions/authenticate``. Los campos
son ``usuario`` (RUC), ``ciAdicional`` (solo para RUC de 13 dígitos) y
``password``. El login **no** está protegido por reCAPTCHA; el captcha aparece
más adelante, en la pantalla de consulta de comprobantes.
"""

from __future__ import annotations

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..logging import obtener_logger
from ..models import Credenciales
from .selectors import Selectores, primer_visible

logger = obtener_logger(__name__)


class LoginError(RuntimeError):
    """El acceso al portal falló."""


def iniciar_sesion(page: Page, credenciales: Credenciales, selectores: Selectores) -> None:
    """Inicia sesión en el portal del SRI.

    Lanza :class:`LoginError` si el formulario no aparece, si las credenciales
    son rechazadas o si la sesión no se establece.
    """
    logger.info("iniciando sesión en el portal", extra={"ruc": credenciales.ruc})

    page.goto(page.url, wait_until="domcontentloaded")

    campo_usuario = primer_visible(page, selectores.grupo("login", "usuario"), timeout_ms=30_000)
    if campo_usuario is None:
        raise LoginError(
            "No se encontró el formulario de acceso del SRI. "
            "Revise los selectores 'login.usuario' en config/selectors.yaml."
        )

    campo_clave = primer_visible(page, selectores.grupo("login", "clave"), timeout_ms=15_000)
    if campo_clave is None:
        raise LoginError("No se encontró el campo de clave del formulario de acceso.")

    campo_usuario.fill(credenciales.ruc)

    if credenciales.requiere_clave_adicional:
        campo_adicional = primer_visible(page, selectores.grupo("login", "clave_adicional"))
        if campo_adicional is None:
            raise LoginError(
                "El RUC tiene 13 dígitos pero no se encontró el campo 'ciAdicional' del formulario."
            )
        campo_adicional.fill(credenciales.clave_adicional)

    campo_clave.fill(credenciales.password)

    boton = primer_visible(page, selectores.grupo("login", "boton"), timeout_ms=10_000)
    if boton is None:
        raise LoginError("No se encontró el botón de ingreso del formulario de acceso.")

    boton.click()

    _esperar_sesion(page, selectores)

    logger.info("sesión iniciada", extra={"ruc": credenciales.ruc, "url": page.url})


def _esperar_sesion(page: Page, selectores: Selectores) -> None:
    """Espera a que el portal acepte las credenciales."""
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        logger.debug("networkidle no alcanzado tras el login", extra={"url": page.url})

    if _mostrar_error(page, selectores):
        raise LoginError("El SRI rechazó las credenciales. Verifique RUC y clave.")

    # Tras un login correcto el portal abandona la pantalla de autenticación.
    if "login-actions" in page.url or "realms" in page.url:
        if _mostrar_error(page, selectores):
            raise LoginError("El SRI rechazó las credenciales. Verifique RUC y clave.")
        raise LoginError(f"El login no avanzó fuera de Keycloak. URL actual: {page.url}")

    if not _mostrar_error(page, selectores):
        logger.info("sesión del portal establecida", extra={"url": page.url})


def _mostrar_error(page: Page, selectores: Selectores) -> bool:
    """Indica si la página muestra un mensaje de error de autenticación."""
    for selector in selectores.grupo("login", "error"):
        try:
            elemento = page.locator(selector).first
            if elemento.count() > 0 and elemento.is_visible():
                texto = elemento.inner_text().strip()
                if texto:
                    logger.warning("mensaje de error en el login", extra={"mensaje": texto})
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False

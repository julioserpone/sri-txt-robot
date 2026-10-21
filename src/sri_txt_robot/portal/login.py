"""Inicio de sesión en el portal del SRI (Keycloak).

El acceso se inicia siempre por
``https://srienlinea.sri.gob.ec/sri-en-linea/contribuyente/perfil``, que
redirige a Keycloak (realm ``Internet``, cliente ``app-sri-claves-angular``).
El formulario lo sirve ``/auth/realms/Internet/login-actions/authenticate`` con
los campos ``usuario`` (RUC de 13 dígitos), ``ciAdicional`` (opcional) y
``password``.

El login **no** está protegido por reCAPTCHA: el captcha aparece más adelante,
en la pantalla de consulta de comprobantes.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..logging import obtener_logger
from ..models import Credenciales
from .selectors import Selectores, primer_visible

logger = obtener_logger(__name__)

#: Segmentos de URL que indican que seguimos dentro de Keycloak.
_SEGMENTOS_KEYCLOAK = ("login-actions", "/realms/")


class LoginError(RuntimeError):
    """El acceso al portal falló."""


def iniciar_sesion(
    page: Page,
    credenciales: Credenciales,
    selectores: Selectores,
    url_entrada: str,
) -> None:
    """Inicia sesión en el portal del SRI.

    Lanza :class:`LoginError` si el formulario no aparece, si las credenciales
    son rechazadas o si la sesión no se establece.
    """
    logger.info("iniciando sesión en el portal", extra={"ruc": credenciales.ruc})

    # La URL de entrada no responde con un documento: el portal redirige al
    # cliente OIDC y la navegación inicial se aborta. Por eso se espera solo el
    # "commit" y no la carga completa: el formulario aparece en la URL de
    # Keycloak que sigue en la cadena.
    try:
        page.goto(url_entrada, wait_until="commit", timeout=60_000)
    except PlaywrightTimeoutError:
        logger.warning("la navegación inicial no terminó; se busca el formulario igualmente")
    except PlaywrightError as error:
        if "ERR_ABORTED" not in str(error):
            raise LoginError(f"No se pudo abrir el portal del SRI: {error}") from error
        logger.debug("navegación inicial abortada por la redirección al SSO")

    campo_usuario = primer_visible(page, selectores.grupo("login", "usuario"), timeout_ms=45_000)

    if campo_usuario is None:
        # Con una sesión guardada todavía válida, el portal no pasa por el
        # formulario: se llega directamente al perfil del contribuyente. Hay
        # que exigir una página real del portal: una sesión caducada deja la
        # pestaña en ``about:blank`` o en una página de error de Chromium.
        if _sesion_reutilizable(page):
            logger.info("sesión del portal reutilizada", extra={"url": page.url})
            return
        raise LoginError(
            "No se encontró el formulario de acceso del SRI. "
            "Revise los selectores 'login.usuario' en config/selectors.yaml."
        )

    campo_clave = primer_visible(page, selectores.grupo("login", "clave"), timeout_ms=15_000)
    if campo_clave is None:
        raise LoginError("No se encontró el campo de clave del formulario de acceso.")

    campo_usuario.fill(credenciales.usuario_portal)

    if credenciales.requiere_clave_adicional:
        campo_adicional = primer_visible(page, selectores.grupo("login", "clave_adicional"))
        if campo_adicional is None:
            raise LoginError("Se configuró 'ciAdicional' pero no se encontró ese campo en el formulario.")
        campo_adicional.fill(credenciales.clave_adicional)

    campo_clave.fill(credenciales.password)

    boton = primer_visible(page, selectores.grupo("login", "boton"), timeout_ms=10_000)
    if boton is None:
        raise LoginError("No se encontró el botón de ingreso del formulario de acceso.")

    try:
        boton.click()
    except PlaywrightTimeoutError:
        # Con un Chrome conectado por CDP, Playwright a veces no recibe el
        # evento de navegación aunque el formulario sí se haya enviado. La
        # comprobación real la hace `_esperar_sesion`.
        logger.debug("el clic de acceso no confirmó la navegación; se comprueba la URL")

    _esperar_sesion(page, selectores)

    logger.info("sesión iniciada", extra={"ruc": credenciales.ruc, "url": page.url})


def _esperar_sesion(page: Page, selectores: Selectores) -> None:
    """Espera a que el portal acepte las credenciales."""
    # Con un perfil ya calentado el portal puede completar la navegación antes
    # de llegar aquí. En ese caso no queda ningún evento de navegación que
    # ``wait_for_url`` pueda observar y la espera agotaría su tiempo aunque la
    # sesión ya esté establecida.
    if not _sesion_reutilizable(page):
        try:
            page.wait_for_url(
                lambda url: _pagina_del_portal(url),
                timeout=90_000,
                wait_until="domcontentloaded",
            )
        except PlaywrightTimeoutError:
            if _mostrar_error(page, selectores):
                raise LoginError("El SRI rechazó las credenciales. Verifique RUC y clave.") from None
            raise LoginError(f"El login no avanzó fuera de Keycloak. URL actual: {page.url}") from None

    if _mostrar_error(page, selectores):
        raise LoginError("El SRI rechazó las credenciales. Verifique RUC y clave.")

    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        logger.debug("networkidle no alcanzado tras el login", extra={"url": page.url})


def _fuera_de_keycloak(url: str) -> bool:
    return not any(segmento in url for segmento in _SEGMENTOS_KEYCLOAK)


def _pagina_del_portal(url: str) -> bool:
    """Indica si la URL es una página HTTP(S) real fuera de Keycloak.

    ``about:blank`` y ``chrome-error://chromewebdata/`` no contienen Keycloak,
    pero tampoco son una sesión válida.
    """
    if not url.startswith(("http://", "https://")):
        return False
    return _fuera_de_keycloak(url)


def _sesion_reutilizable(page: Page) -> bool:
    """Indica si la pestaña quedó en una página autenticada del portal."""
    url = page.url
    if not _pagina_del_portal(url):
        return False
    # La entrada canónica es /sri-en-linea/contribuyente/perfil; la app de
    # comprobantes vive en /comprobantes-electronicos-internet/. Cualquier
    # página del portal sirve como prueba de sesión, salvo el propio SSO.
    return "srienlinea.sri.gob.ec" in url


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

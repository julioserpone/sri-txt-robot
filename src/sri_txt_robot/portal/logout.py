"""Cierre de sesión en el portal del SRI.

El portal expone "Cerrar sesión" dentro del menú de la barra superior (icono
``more_vert``): ``#topbar-menu-button`` despliega ``#topbar-menu_ul`` y el
enlace ``a.boton-cerrar-legado`` ejecuta
``location.replace('/tuportal-internet/salir.jspa')``, que invalida la sesión
de Keycloak.

Cerrar la sesión al terminar evita dejar cookies vivas en el ``storage_state``
y reduce el riesgo de que el portal invalide el acceso por sesiones
concurrentes.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from ..logging import obtener_logger
from .selectors import Selectores, primer_visible

logger = obtener_logger(__name__)


def cerrar_sesion(page: Page, selectores: Selectores) -> bool:
    """Cierra la sesión del portal. Devuelve ``True`` si lo consiguió.

    No lanza excepciones: el cierre es una tarea de limpieza y un fallo aquí no
    debe ocultar el resultado de las descargas.
    """
    if page.is_closed():
        return False

    try:
        enlace = _enlace_cerrar_sesion(page, selectores)
        if enlace is None:
            logger.warning("no se encontró la opción 'Cerrar sesión' en el portal")
            return False

        logger.info("cerrando sesión en el portal")
        enlace.click(timeout=15_000)

        # El portal navega a salir.jspa; se espera a que la sesión quede atrás
        # sin depender de un tiempo fijo.
        page.wait_for_timeout(1_500)
        _ = page.url
        return True
    except PlaywrightError as error:
        logger.warning("no se pudo cerrar la sesión", extra={"error": str(error)[:200]})
        return False
    except Exception as error:  # noqa: BLE001 - limpieza best-effort
        logger.warning("error inesperado al cerrar la sesión", extra={"error": str(error)[:200]})
        return False


def _enlace_cerrar_sesion(page: Page, selectores: Selectores) -> Locator | None:
    """Devuelve el enlace de cierre, desplegando el menú si hace falta."""
    enlace = primer_visible(page, selectores.grupo("sesion", "cerrar"), timeout_ms=2_000)
    if enlace is not None:
        return enlace

    boton_menu = primer_visible(page, selectores.grupo("sesion", "menu"), timeout_ms=10_000)
    if boton_menu is None:
        return None

    boton_menu.click(timeout=10_000)
    return primer_visible(page, selectores.grupo("sesion", "cerrar"), timeout_ms=10_000)

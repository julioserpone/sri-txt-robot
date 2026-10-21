"""Ciclo de vida del navegador.

En AWS Lambda el navegador corre dentro de una imagen de contenedor; por eso
se activan las banderas habituales para entornos sin ``/dev/shm`` amplio y sin
privilegios de sandbox.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from ..config import PortalConfig
from ..logging import obtener_logger

logger = obtener_logger(__name__)

#: Banderas necesarias para ejecutar Chromium dentro de un contenedor.
BANDERAS = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
)


class Navegador:
    """Envoltorio del contexto de Playwright con persistencia opcional.

    Guarda el ``storage_state`` (cookies) para reutilizar la sesión entre
    ejecuciones: reduce la cantidad de inicios de sesión y el riesgo de que el
    portal marque la cuenta como automatizada.
    """

    def __init__(self, configuracion: PortalConfig, estado: Path | None = None) -> None:
        self._configuracion = configuracion
        self._estado = estado
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexto: BrowserContext | None = None
        self.pagina: Page | None = None

    def __enter__(self) -> Navegador:
        self._playwright = sync_playwright().start()

        self._browser = self._playwright.chromium.launch(
            headless=self._configuracion.headless,
            args=list(BANDERAS),
        )

        opciones: dict[str, object] = {
            "locale": self._configuracion.locale,
            "timezone_id": self._configuracion.timezone,
            "viewport": {
                "width": self._configuracion.viewport_width,
                "height": self._configuracion.viewport_height,
            },
        }
        if self._configuracion.user_agent:
            opciones["user_agent"] = self._configuracion.user_agent
        if self._estado is not None and self._estado.is_file():
            opciones["storage_state"] = str(self._estado)
            logger.info("reutilizando la sesión guardada", extra={"estado": str(self._estado)})

        self._contexto = self._browser.new_context(**opciones)  # type: ignore[arg-type]
        self._contexto.set_default_timeout(self._configuracion.timeout_ms)
        self._contexto.set_default_navigation_timeout(self._configuracion.navigation_timeout_ms)
        self.pagina = self._contexto.new_page()

        return self

    def __exit__(
        self,
        tipo_exc: type[BaseException] | None,
        valor: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        if self._contexto is not None:
            try:
                if self._estado is not None:
                    self._estado.parent.mkdir(parents=True, exist_ok=True)
                    self._contexto.storage_state(path=str(self._estado))
            except Exception:  # noqa: BLE001
                logger.warning("no se pudo guardar el estado de la sesión", exc_info=True)
            self._contexto.close()

        if self._browser is not None:
            self._browser.close()

        if self._playwright is not None:
            self._playwright.stop()

    @property
    def contexto(self) -> BrowserContext:
        if self._contexto is None:
            raise RuntimeError("El navegador no está iniciado.")
        return self._contexto

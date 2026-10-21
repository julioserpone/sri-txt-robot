"""Ciclo de vida del navegador.

En AWS Lambda el navegador corre dentro de una imagen de contenedor; por eso
se activan las banderas habituales para entornos sin ``/dev/shm`` amplio y sin
privilegios de sandbox.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import TracebackType

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from ..config import PortalConfig
from ..logging import obtener_logger

logger = obtener_logger(__name__)

#: Ruta habitual de Chrome en macOS. Se usa cuando ``portal.perfil_dir`` está
#: definido y no se indicó ``portal.chrome_path``.
CHROME_MACOS = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: Tiempo máximo de espera a que el Chrome recién lanzado abra el puerto CDP.
ESPERA_CDP_SEGUNDOS = 40.0

#: Banderas necesarias para ejecutar Chromium dentro de un contenedor.
BANDERAS = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--no-first-run",
    "--no-default-browser-check",
    # El portal del SRI queda colgado si Chromium negocia HTTP/2: la petición
    # nunca recibe respuesta (y acaba en chrome-error://chromewebdata/). Con
    # HTTP/1.1 la portada responde en décimas de segundo.
    "--disable-http2",
)

#: User-Agent de un Chrome de escritorio. El portal (o su WAF) no responde a
#: navegadores marcados como headless, así que se usa este valor cuando la
#: configuración no define uno.
USER_AGENT_POR_DEFECTO = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
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
        self._guardar_estado = True
        self._proceso: subprocess.Popen[bytes] | None = None
        self._modo_perfil = bool(configuracion.perfil_dir.strip())
        self.pagina: Page | None = None

    def __enter__(self) -> Navegador:
        self._playwright = sync_playwright().start()

        if self._modo_perfil:
            self._contexto = self._abrir_perfil()
        else:
            self._browser = self._playwright.chromium.launch(
                headless=self._configuracion.headless,
                args=list(BANDERAS),
            )
            self._contexto = self._browser.new_context(**self._opciones_contexto())  # type: ignore[arg-type]

        self._contexto.set_default_timeout(self._configuracion.timeout_ms)
        self._contexto.set_default_navigation_timeout(self._configuracion.navigation_timeout_ms)
        paginas = self._contexto.pages
        self.pagina = paginas[0] if paginas else self._contexto.new_page()

        return self

    def _opciones_contexto(self) -> dict[str, object]:
        opciones: dict[str, object] = {
            "locale": self._configuracion.locale,
            "timezone_id": self._configuracion.timezone,
            "user_agent": self._configuracion.user_agent or USER_AGENT_POR_DEFECTO,
            "viewport": {
                "width": self._configuracion.viewport_width,
                "height": self._configuracion.viewport_height,
            },
        }
        if self._estado is not None and self._estado.is_file():
            opciones["storage_state"] = str(self._estado)
            logger.info("reutilizando la sesión guardada", extra={"estado": str(self._estado)})
        return opciones

    def _abrir_perfil(self) -> BrowserContext:
        """Lanza Chrome con un perfil dedicado y se conecta por CDP.

        Playwright añade ``--enable-automation`` y ``--remote-debugging-pipe``,
        banderas que Google usa para puntuar el reCAPTCHA: con ellas, incluso un
        perfil ya validado por una persona vuelve a recibir "Captcha incorrecta".
        Por eso el navegador se lanza aquí, sin esas banderas, y Playwright solo
        se conecta al puerto de depuración.
        """
        assert self._playwright is not None
        perfil = Path(self._configuracion.perfil_dir).expanduser()
        perfil.mkdir(parents=True, exist_ok=True)

        ejecutable = self._configuracion.chrome_path.strip()
        if not ejecutable:
            if sys.platform == "darwin" and Path(CHROME_MACOS).is_file():
                ejecutable = CHROME_MACOS
            else:
                ejecutable = self._playwright.chromium.executable_path

        if self._configuracion.headless and sys.platform == "darwin":
            logger.warning("el perfil dedicado necesita una ventana visible para el captcha; se ignora headless")

        puerto = _puerto_libre()
        orden = [
            ejecutable,
            f"--remote-debugging-port={puerto}",
            f"--user-data-dir={perfil}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={self._configuracion.viewport_width},{self._configuracion.viewport_height}",
            "about:blank",
        ]
        if sys.platform != "darwin":
            orden[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
        if self._configuracion.headless and sys.platform != "darwin":
            orden.insert(-1, "--headless=new")

        logger.info("abriendo Chrome con el perfil dedicado", extra={"perfil": str(perfil), "puerto": puerto})
        self._proceso = subprocess.Popen(orden, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url_cdp = _esperar_cdp(puerto, self._proceso)

        self._browser = self._playwright.chromium.connect_over_cdp(url_cdp)
        contextos = self._browser.contexts
        if not contextos:
            raise RuntimeError("Chrome no expuso ningún contexto de navegador.")
        return contextos[0]

    def __exit__(
        self,
        tipo_exc: type[BaseException] | None,
        valor: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        if self._contexto is not None and not self._modo_perfil:
            try:
                if self._estado is not None and self._guardar_estado:
                    self._estado.parent.mkdir(parents=True, exist_ok=True)
                    self._contexto.storage_state(path=str(self._estado))
            except Exception:  # noqa: BLE001
                logger.warning("no se pudo guardar el estado de la sesión", exc_info=True)

        try:
            if self._browser is not None:
                # Con CDP, cerrar el navegador apaga el Chrome que lanzamos.
                self._browser.close()
            elif self._contexto is not None:
                self._contexto.close()
        except Exception:  # noqa: BLE001
            logger.debug("el navegador ya estaba cerrado", exc_info=True)

        if self._proceso is not None:
            _cerrar_proceso(self._proceso)
            self._proceso = None

        if self._playwright is not None:
            self._playwright.stop()

    def olvidar_estado(self) -> None:
        """No guarda el ``storage_state`` al cerrar y borra el anterior.

        Se usa tras cerrar sesión en el portal: las cookies ya no sirven y
        reutilizarlas solo provoca un salto inútil por Keycloak.
        """
        self._guardar_estado = False
        if self._estado is not None:
            try:
                self._estado.unlink(missing_ok=True)
            except OSError:
                logger.debug("no se pudo borrar el estado de la sesión", exc_info=True)

    @property
    def contexto(self) -> BrowserContext:
        if self._contexto is None:
            raise RuntimeError("El navegador no está iniciado.")
        return self._contexto

    @property
    def usa_perfil(self) -> bool:
        """Indica si el navegador se abrirá con el perfil dedicado (modo CDP)."""
        return self._modo_perfil


def _puerto_libre() -> int:
    """Devuelve un puerto TCP libre en la interfaz local."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conexion:
        conexion.bind(("127.0.0.1", 0))
        return int(conexion.getsockname()[1])


def _esperar_cdp(puerto: int, proceso: subprocess.Popen[bytes]) -> str:
    """Espera a que el Chrome lanzado publique el endpoint CDP."""
    url = f"http://127.0.0.1:{puerto}/json/version"
    limite = time.monotonic() + ESPERA_CDP_SEGUNDOS
    while time.monotonic() < limite:
        if proceso.poll() is not None:
            raise RuntimeError(f"Chrome terminó antes de exponer CDP (código {proceso.returncode}).")
        try:
            with urllib.request.urlopen(url, timeout=1) as respuesta:
                respuesta.read(1)
            return url
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise RuntimeError(f"Chrome no expuso el puerto CDP {puerto} en {ESPERA_CDP_SEGUNDOS:.0f} s.")


def _cerrar_proceso(proceso: subprocess.Popen[bytes]) -> None:
    """Termina el Chrome lanzado para el perfil dedicado."""
    if proceso.poll() is not None:
        return
    proceso.terminate()
    try:
        proceso.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proceso.kill()

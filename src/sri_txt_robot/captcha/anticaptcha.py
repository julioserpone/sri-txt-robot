"""Cliente de la API de anti-captcha para resolver reCAPTCHA.

Anti-captcha expone una API JSON muy simple:

1. ``createTask`` devuelve un ``taskId``.
2. ``getTaskResult`` se consulta hasta que ``status`` sea ``ready``.

El token devuelto (campo ``solution.gRecaptchaResponse``) es un valor de
``g-recaptcha-response`` válido durante aproximadamente dos minutos, por lo que
debe resolverse justo antes de enviar la consulta al portal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..logging import obtener_logger

logger = obtener_logger(__name__)

#: Errores que conviene reintentar con otro tipo de tarea o más adelante.
ERRORES_REINTENTABLES = frozenset(
    {
        "ERROR_NO_SLOT_AVAILABLE",
        "ERROR_CAPTCHA_UNSOLVABLE",
        "ERROR_RECAPTCHA_TIMEOUT",
        "ERROR_FAILED_LOADING_WIDGET",
        "ERROR_TOKEN_EXPIRED",
        "ERROR_ALL_WORKERS_FILTERED",
    }
)

#: Errores que no tienen sentido reintentar: la configuración está mal.
ERRORES_FATALES = frozenset(
    {
        "ERROR_KEY_DOES_NOT_EXIST",
        "ERROR_ZERO_BALANCE",
        "ERROR_IP_NOT_ALLOWED",
        "ERROR_ACCOUNT_SUSPENDED",
        "ERROR_INVALID_KEY_TYPE",
        "ERROR_TASK_NOT_SUPPORTED",
    }
)


class AntiCaptchaError(RuntimeError):
    """Fallo irrecuperable al interactuar con anti-captcha."""


class CaptchaNoResueltoError(AntiCaptchaError):
    """No se obtuvo un token válido dentro del tiempo esperado."""


@dataclass(frozen=True, slots=True)
class SolucionCaptcha:
    """Token de reCAPTCHA listo para inyectar en el formulario."""

    token: str
    task_type: str
    task_id: int
    user_agent: str = ""
    coste: float = 0.0

    @property
    def tiene_user_agent(self) -> bool:
        return bool(self.user_agent)


class AntiCaptchaClient:
    """Cliente HTTP de anti-captcha."""

    def __init__(
        self,
        api_key: str,
        *,
        api_url: str = "https://api.anti-captcha.com",
        timeout: float = 30.0,
    ) -> None:
        if not api_key.strip():
            raise AntiCaptchaError("La clave de anti-captcha no puede estar vacía.")
        self._api_key = api_key
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout

    # -- API pública ----------------------------------------------------

    def saldo(self) -> float:
        """Saldo disponible en la cuenta de anti-captcha."""
        respuesta = self._post("/getBalance", {})
        self._verificar_error(respuesta)
        return float(respuesta.get("balance", 0.0))

    def resolver(
        self,
        *,
        website_url: str,
        website_key: str,
        task_types: tuple[str, ...],
        poll_interval: float = 3.0,
        poll_timeout: float = 180.0,
        is_invisible: bool | None = None,
        page_action: str | None = None,
    ) -> SolucionCaptcha:
        """Resuelve el reCAPTCHA de la página indicada.

        Se prueban los tipos de tarea en orden hasta obtener uno válido. Esto
        permite que el robot funcione tanto con reCAPTCHA estándar como con
        reCAPTCHA Enterprise, y con variantes visibles e invisibles.
        """
        if not website_key.strip():
            raise AntiCaptchaError("No se pudo determinar la clave pública del reCAPTCHA.")

        errores: list[str] = []

        for task_type in task_types:
            try:
                return self._resolver_con_tipo(
                    task_type=task_type,
                    website_url=website_url,
                    website_key=website_key,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    is_invisible=is_invisible,
                    page_action=page_action,
                )
            except AntiCaptchaError as error:
                codigo = getattr(error, "codigo", "")
                logger.warning(
                    "tipo de tarea no viable",
                    extra={"task_type": task_type, "error": str(error), "codigo": codigo},
                )
                errores.append(f"{task_type}: {error}")
                if codigo in ERRORES_FATALES:
                    raise

        raise CaptchaNoResueltoError(
            "No se pudo resolver el reCAPTCHA con ningún tipo de tarea. " + " | ".join(errores)
        )

    def reportar_incorrecto(self, task_id: int) -> None:
        """Informa que el token fue rechazado por el sitio objetivo."""
        try:
            self._post("/reportIncorrectRecaptcha", {"taskId": task_id})
        except AntiCaptchaError:
            logger.warning("no se pudo reportar el captcha como incorrecto", extra={"task_id": task_id})

    def reportar_correcto(self, task_id: int) -> None:
        """Informa que el token fue aceptado por el sitio objetivo."""
        try:
            self._post("/reportCorrectRecaptcha", {"taskId": task_id})
        except AntiCaptchaError:
            logger.warning("no se pudo reportar el captcha como correcto", extra={"task_id": task_id})

    # -- Interno --------------------------------------------------------

    def _resolver_con_tipo(
        self,
        *,
        task_type: str,
        website_url: str,
        website_key: str,
        poll_interval: float,
        poll_timeout: float,
        is_invisible: bool | None,
        page_action: str | None,
    ) -> SolucionCaptcha:
        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        if is_invisible is not None and "V2" in task_type:
            task["isInvisible"] = is_invisible
        if page_action and "V3" in task_type:
            task["pageAction"] = page_action

        creacion = self._post("/createTask", {"task": task})
        self._verificar_error(creacion)

        task_id = creacion.get("taskId")
        if task_id is None:
            raise AntiCaptchaError("anti-captcha no devolvió taskId al crear la tarea.")

        limite = time.monotonic() + poll_timeout
        # Espera mínima antes del primer sondeo: los trabajadores tardan unos
        # segundos en tomar la tarea.
        time.sleep(min(poll_interval, 5.0))

        while time.monotonic() < limite:
            resultado = self._post("/getTaskResult", {"taskId": task_id})
            self._verificar_error(resultado)

            estado = resultado.get("status")
            if estado == "ready":
                solucion = resultado.get("solution") or {}
                token = str(solucion.get("gRecaptchaResponse", ""))
                if not token:
                    raise AntiCaptchaError("anti-captcha devolvió una solución sin token.")
                return SolucionCaptcha(
                    token=token,
                    task_type=task_type,
                    task_id=int(task_id),
                    user_agent=str(solucion.get("userAgent", "")),
                    coste=float(resultado.get("cost", 0.0) or 0.0),
                )
            if estado != "processing":
                raise AntiCaptchaError(f"Estado inesperado de la tarea: {estado!r}")

            time.sleep(poll_interval)

        raise CaptchaNoResueltoError(f"La tarea {task_id} no se resolvió en {poll_timeout:.0f} s.")

    def _post(self, ruta: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._api_url}{ruta}"
        payload = {"clientKey": self._api_key, **cuerpo}

        try:
            respuesta = httpx.post(url, json=payload, timeout=self._timeout)
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise AntiCaptchaError(f"Error HTTP {error.response.status_code} en {ruta}.") from error
        except httpx.HTTPError as error:
            raise AntiCaptchaError(f"Error de red en {ruta}: {error}") from error

        try:
            datos: dict[str, Any] = respuesta.json()
        except ValueError as error:
            raise AntiCaptchaError(f"Respuesta no JSON en {ruta}.") from error

        return datos

    def _verificar_error(self, respuesta: dict[str, Any]) -> None:
        error_id = int(respuesta.get("errorId", 0) or 0)
        if error_id == 0:
            return

        codigo = str(respuesta.get("errorCode", f"ERROR_{error_id}"))
        descripcion = str(respuesta.get("errorDescription", "error desconocido"))
        error = AntiCaptchaError(f"{codigo}: {descripcion}")
        error.codigo = codigo  # type: ignore[attr-defined]
        raise error

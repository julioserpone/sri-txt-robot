"""Notificación HTTP firmada al finalizar una invocación.

La invocación asíncrona de Lambda solo devuelve un ``RequestId``; quien llama
no sabe si la descarga funcionó. Para cerrar ese hueco, la función puede
enviar un callback HTTP firmado al terminar (con éxito, parcial o con error).

Contrato (versión 1):

- ``POST`` a ``SRI_CALLBACK_URL`` con ``Content-Type: application/json``.
- Cabeceras ``X-Sri-Event-Id``, ``X-Sri-Timestamp``, ``X-Sri-Signature`` y
  ``X-Sri-Delivery-Attempt``.
- Firma: ``sha256=hex(HMAC-SHA256(secreto, f"{timestamp}.{cuerpo}"))``.
- Cuerpo JSON con ``event_id``, ``timestamp``, ``status`` y el resumen.
- Reintentos con espera exponencial ante errores de red, 408, 425, 429 y 5xx.
  Un 4xx distinto de 408/425/429 no se reintenta: el receptor lo rechazó.

La URL y el secreto se leen del entorno de la función para que no viajen en
el evento. Un evento puede proponer otra URL solo si su host aparece en
``SRI_CALLBACK_ALLOWED_HOSTS``; así el robot no se convierte en un emisor de
firmas hacia un destino arbitrario.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .logging import obtener_logger

logger = obtener_logger(__name__)

CABECERA_EVENTO = "X-Sri-Event-Id"
CABECERA_TIMESTAMP = "X-Sri-Timestamp"
CABECERA_FIRMA = "X-Sri-Signature"
CABECERA_INTENTO = "X-Sri-Delivery-Attempt"

#: Longitud mínima del secreto compartido. Un secreto corto no protege la firma.
SECRETO_MINIMO = 16

#: Estados que sí admiten reintento: el receptor puede recuperarse.
_REINTENTABLES = frozenset({408, 425, 429})


def firmar(secreto: str, timestamp: int, cuerpo: bytes) -> str:
    """Calcula la firma HMAC-SHA256 de ``{timestamp}.{cuerpo}``."""
    mensaje = f"{timestamp}.".encode() + cuerpo
    return hmac.new(secreto.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class ConfigCallback:
    """Parámetros de entrega del callback."""

    url: str
    secreto: str
    timeout: float = 10.0
    intentos: int = 3
    espera_base: float = 1.0

    @classmethod
    def desde_entorno(cls, url_evento: str = "") -> ConfigCallback | None:
        """Construye la configuración; ``None`` si el callback no está activo.

        ``url_evento`` solo se acepta cuando su host está autorizado en
        ``SRI_CALLBACK_ALLOWED_HOSTS``. Si no hay URL o secreto, el callback
        queda desactivado sin romper la ejecución del robot.
        """
        url = os.environ.get("SRI_CALLBACK_URL", "").strip()
        secreto = os.environ.get("SRI_CALLBACK_SECRET", "").strip()

        if url_evento:
            permitidos = _hosts_permitidos()
            host = (urlparse(url_evento).hostname or "").lower()
            if host and host in permitidos:
                url = url_evento
            else:
                logger.warning(
                    "callback_url del evento ignorada: host no autorizado",
                    extra={"host": host},
                )

        if not url and not secreto:
            return None
        if not url:
            logger.warning("callback deshabilitado: falta SRI_CALLBACK_URL")
            return None
        if not secreto:
            logger.warning("callback deshabilitado: falta SRI_CALLBACK_SECRET")
            return None

        _validar_url(url)
        if len(secreto) < SECRETO_MINIMO:
            raise ValueError(f"SRI_CALLBACK_SECRET debe tener al menos {SECRETO_MINIMO} caracteres.")

        return cls(
            url=url,
            secreto=secreto,
            timeout=_flotante("SRI_CALLBACK_TIMEOUT_SECONDS", 10.0, minimo=1.0),
            intentos=int(_flotante("SRI_CALLBACK_ATTEMPTS", 3, minimo=1)),
            espera_base=_flotante("SRI_CALLBACK_BACKOFF_SECONDS", 1.0, minimo=0.0),
        )


@dataclass(frozen=True, slots=True)
class Entrega:
    """Resultado de intentar entregar el callback."""

    enviado: bool
    intentos: int
    status: int | None = None
    error: str = ""

    def a_dict(self) -> dict[str, Any]:
        return {
            "enviado": self.enviado,
            "intentos": self.intentos,
            "status": self.status,
            "error": self.error,
        }


class Notificador:
    """Entrega el callback con reintentos acotados por el tiempo restante."""

    def __init__(
        self,
        config: ConfigCallback,
        *,
        transporte: Callable[..., Any] | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._transporte = transporte or httpx.post
        self._dormir = dormir
        self._reloj = reloj

    def enviar(self, event_id: str, cuerpo: dict[str, Any], plazo_ms: int | None = None) -> Entrega:
        """Envía el callback y devuelve el resultado de la última tentativa.

        ``plazo_ms`` es el tiempo restante de la invocación. Se reservan dos
        segundos para que el handler pueda terminar y registrar el resultado.
        """
        limite = None
        if plazo_ms is not None:
            limite = self._reloj() + max(plazo_ms / 1000 - 2.0, 0.0)

        ultima = Entrega(enviado=False, intentos=0, error="sin tiempo para el callback")

        for intento in range(1, self._config.intentos + 1):
            timeout = self._timeout(limite)
            if timeout is None:
                break

            timestamp = int(time.time())
            datos = json.dumps(
                {**cuerpo, "timestamp": timestamp},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            cabeceras = {
                "Content-Type": "application/json",
                "User-Agent": "sri-txt-robot/callback-v1",
                CABECERA_EVENTO: event_id,
                CABECERA_TIMESTAMP: str(timestamp),
                CABECERA_FIRMA: f"sha256={firmar(self._config.secreto, timestamp, datos)}",
                CABECERA_INTENTO: str(intento),
            }

            try:
                respuesta = self._transporte(
                    self._config.url,
                    content=datos,
                    headers=cabeceras,
                    timeout=timeout,
                )
            except Exception as error:  # noqa: BLE001 - cualquier fallo de red se reintenta
                ultima = Entrega(False, intento, None, _texto_error(error))
            else:
                status = int(respuesta.status_code)
                if 200 <= status < 300:
                    logger.info(
                        "callback entregado",
                        extra={"event_id": event_id, "status": status, "intentos": intento},
                    )
                    return Entrega(True, intento, status)
                ultima = Entrega(False, intento, status, f"HTTP {status}")
                if not _reintentable(status):
                    logger.warning(
                        "callback rechazado sin reintento",
                        extra={"event_id": event_id, "status": status},
                    )
                    return ultima

            logger.warning(
                "fallo al entregar el callback",
                extra={"event_id": event_id, "intento": intento, "error": ultima.error},
            )

            if intento == self._config.intentos:
                break
            espera = self._config.espera_base * (2 ** (intento - 1))
            if limite is not None and self._reloj() + espera >= limite:
                break
            if espera > 0:
                self._dormir(espera)
            if limite is not None and self._reloj() >= limite:
                break

        return ultima

    def _timeout(self, limite: float | None) -> float | None:
        """Timeout de la tentativa, recortado al tiempo que queda."""
        if limite is None:
            return self._config.timeout
        restante = limite - self._reloj()
        if restante <= 1.0:
            return None
        return min(self._config.timeout, restante)


def _reintentable(status: int) -> bool:
    return status in _REINTENTABLES or status >= 500


def _texto_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:300]


def _hosts_permitidos() -> set[str]:
    crudos = os.environ.get("SRI_CALLBACK_ALLOWED_HOSTS", "")
    return {host.strip().lower() for host in crudos.split(",") if host.strip()}


def _validar_url(url: str) -> None:
    partes = urlparse(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        raise ValueError(f"URL de callback inválida: {url!r}")
    if partes.scheme == "http" and partes.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("La URL de callback debe usar https (solo se admite http en localhost).")


def _flotante(nombre: str, defecto: float, *, minimo: float) -> float:
    crudo = os.environ.get(nombre, "").strip()
    if not crudo:
        return defecto
    try:
        valor = float(crudo)
    except ValueError as error:
        raise ValueError(f"{nombre} debe ser numérico: {crudo!r}") from error
    if valor < minimo:
        raise ValueError(f"{nombre} debe ser mayor o igual a {minimo}.")
    return valor

"""Logging estructurado en JSON, compatible con CloudWatch Logs."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_CONFIGURADO = False


class JsonFormatter(logging.Formatter):
    """Serializa cada registro como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "nivel": record.levelname,
            "mensaje": record.getMessage(),
            "logger": record.name,
        }

        for clave, valor in record.__dict__.items():
            if clave.startswith("_") or clave in logging.LogRecord("", 0, "", 0, "", None, None).__dict__:
                continue
            if clave in {"args", "msg", "exc_info", "exc_text", "stack_info", "levelname", "name"}:
                continue
            payload[clave] = valor

        if record.exc_info is not None:
            payload["excepcion"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configurar_logging(nivel: str = "INFO") -> None:
    """Configura el logging raíz con formato JSON. Es idempotente."""
    global _CONFIGURADO
    if _CONFIGURADO:
        return

    manejador = logging.StreamHandler(stream=sys.stdout)
    manejador.setFormatter(JsonFormatter())

    raiz = logging.getLogger()
    raiz.handlers = [manejador]
    raiz.setLevel(nivel.upper())

    # Playwright es muy verboso en depuración.
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURADO = True


def obtener_logger(nombre: str) -> logging.Logger:
    """Devuelve un logger con el formato global ya configurado."""
    configurar_logging()
    return logging.getLogger(nombre)

"""Punto de entrada AWS Lambda.

La invocación es un evento de EventBridge Scheduler. Ejemplo::

    {
      "periodo": "2026-09",
      "rucs": ["1790012345001"],
      "tipos": ["01", "05"]
    }

Las credenciales de los contribuyentes se leen de AWS Secrets Manager (JSON
``{"<ruc>": "<clave>"}``) y se inyectan como variables de entorno
``SRI_PASSWORD_<RUC>`` antes de construir la configuración.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from typing import Any

from .config import RobotConfig, cargar_config
from .logging import obtener_logger
from .models import Periodo, TipoComprobante
from .pipeline import Opciones, Robot
from .portal.selectors import Selectores
from .storage import crear_almacenamiento

logger = obtener_logger(__name__)

CONFIG_POR_DEFECTO = "/opt/config/robots.yaml"
SELECTORES_POR_DEFECTO = "/opt/config/selectors.yaml"
SECRETS_POR_DEFECTO = "/opt/config/secrets.json"


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Ejecuta el robot para el período indicado en el evento."""
    configuracion = _cargar_configuracion()
    selectores = Selectores.cargar(os.environ.get("SRI_ROBOT_SELECTORS", SELECTORES_POR_DEFECTO))
    almacenamiento = crear_almacenamiento(configuracion)

    periodos = _periodos(event)
    tipos = tuple(TipoComprobante.desde_codigo(codigo) for codigo in event.get("tipos", []) or [])

    robot = Robot(configuracion, selectores, almacenamiento)
    resumen = robot.ejecutar(
        Opciones(
            periodos=periodos,
            tipos=tipos,
            ruc=str(event.get("ruc", "")),
            forzar=bool(event.get("forzar", False)),
        )
    )

    respuesta = {
        "periodos": [periodo.clave for periodo in periodos],
        "ejecutadoEn": datetime.now(tz=UTC).isoformat(),
        **resumen.a_dict(),
    }

    logger.info(
        "ejecución finalizada",
        extra={"periodos": [periodo.clave for periodo in periodos], "fallos": len(resumen.fallos)},
    )
    return respuesta


def _cargar_configuracion() -> RobotConfig:
    """Carga la configuración e inyecta las claves de los contribuyentes."""
    _inyectar_secretos()

    ruta = os.environ.get("SRI_ROBOT_CONFIG", CONFIG_POR_DEFECTO)
    configuracion = cargar_config(ruta)

    # Valida que todas las credenciales Referential estén disponibles.
    for entrada in configuracion.robots:
        if entrada.activo:
            entrada.a_config_ruc()

    return configuracion


def _inyectar_secretos() -> None:
    """Lee el secreto de Secrets Manager y exporta las claves por RUC."""
    arn = os.environ.get("SRI_ROBOT_SECRETS_ARN", "").strip()
    if not arn:
        return

    import boto3

    cliente = boto3.client("secretsmanager")
    respuesta = cliente.get_secret_value(SecretId=arn)
    secreto = json.loads(respuesta["SecretString"])

    if not isinstance(secreto, dict):
        raise ValueError("El secreto debe ser un mapa JSON {ruc: clave}.")

    for ruc, clave in secreto.items():
        variable = f"SRI_PASSWORD_{ruc}"
        if variable not in os.environ:
            os.environ[variable] = str(clave)

    logger.info("secretos inyectados", extra={"cantidad": len(secreto)})


def _periodos(event: dict[str, Any]) -> tuple[Periodo, ...]:
    """Resuelve los períodos del evento; por defecto, el mes anterior.

    Acepta ``"periodos": ["2026-07", "2026-08"]`` y también ``"periodo"`` para
    los eventos antiguos de un solo mes.
    """
    crudos = event.get("periodos")
    if isinstance(crudos, str):
        crudos = [crudos]
    if not crudos and event.get("periodo"):
        crudos = [str(event["periodo"])]

    periodos: list[Periodo] = []
    vistos: set[str] = set()
    for crudo in crudos or []:
        periodo = Periodo.desde_texto(str(crudo))
        if periodo.clave in vistos:
            continue
        vistos.add(periodo.clave)
        periodos.append(periodo)

    if periodos:
        return tuple(periodos)

    hoy = date.today()
    if hoy.month == 1:
        return (Periodo(anio=hoy.year - 1, mes=12),)
    return (Periodo(anio=hoy.year, mes=hoy.month - 1),)

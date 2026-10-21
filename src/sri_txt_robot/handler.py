"""Punto de entrada AWS Lambda.

La invocación es un evento de EventBridge Scheduler. Ejemplo::

    {
      "periodo": "2026-09",
      "ruc": "1758871873001",
      "tipos": ["01"]
    }

Las credenciales **no** se guardan en Secrets Manager ni en la imagen: la clave
del contribuyente llega en la variable de entorno que declare
``robots[].password_env`` (por ejemplo ``SRI_PASSWORD``), configurada en la
propia función de Lambda.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
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


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Ejecuta el robot para el período indicado en el evento."""
    configuracion = _cargar_configuracion()
    _preparar_perfil(configuracion)
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
        "ambiente": configuracion.storage.ambiente,
        "periodos": [periodo.clave for periodo in periodos],
        "ejecutadoEn": datetime.now(tz=UTC).isoformat(),
        **resumen.a_dict(),
    }

    logger.info(
        "ejecución finalizada",
        extra={
            "ambiente": configuracion.storage.ambiente,
            "periodos": [periodo.clave for periodo in periodos],
            "fallos": len(resumen.fallos),
        },
    )
    return respuesta


def _preparar_perfil(configuracion: RobotConfig) -> None:
    """Copia el perfil horneado en la imagen a una ruta escribible.

    En Lambda ``/opt`` es de solo lectura y Chrome necesita escribir en el
    perfil. La copia se hace al empezar la ejecución, no al arrancar el
    contenedor: la fase de inicialización tiene un límite de 10 s y el perfil
    ronda los 40 MB. Se copia a un directorio temporal y se renombra para no
    dejar un perfil a medias si la ejecución se interrumpe.
    """
    origen_texto = os.environ.get("SRI_PERFIL_ORIGEN", "").strip()
    if not origen_texto:
        return
    origen = Path(origen_texto).expanduser()
    if not origen.is_dir():
        return

    destino = Path(configuracion.portal.perfil_dir).expanduser()
    if destino.exists():
        return

    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(f"{destino.name}-tmp")
    shutil.rmtree(temporal, ignore_errors=True)
    shutil.copytree(origen, temporal, symlinks=True)
    temporal.rename(destino)
    logger.info("perfil de Chrome copiado a una ruta escribible", extra={"perfil": str(destino)})


def _cargar_configuracion() -> RobotConfig:
    """Carga la configuración y valida que las claves estén disponibles."""
    ruta = os.environ.get("SRI_ROBOT_CONFIG", CONFIG_POR_DEFECTO)
    configuracion = cargar_config(ruta)

    for entrada in configuracion.robots:
        if entrada.activo:
            entrada.a_config_ruc()

    return configuracion


def _periodos(event: dict[str, Any]) -> tuple[Periodo, ...]:
    """Resuelve los períodos del evento; por defecto, el mes anterior.

    Acepta ``"periodos": ["2026-07", "2026-08"]`` y también ``"periodo"`` para
    los eventos de un solo mes.
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

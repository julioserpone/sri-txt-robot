"""Punto de entrada AWS Lambda.

La invocación puede ser un evento de EventBridge o una llamada desde otra
aplicación. Ejemplo::

    {
      "periodo": "2026-09",
      "ruc": "1758871873001",
      "tipos": ["01"],
      "sealed_credentials": "base64(sodium_crypto_box_seal(...))"
    }

Hay dos modos de credenciales:

- **Estático:** ``robots[].password`` o ``robots[].password_env`` en el YAML,
  normalmente ``SRI_PASSWORD`` como variable de entorno de la función.
- **Dinámico:** el evento trae ``sealed_credentials`` cifrado con la clave
  pública del robot. La clave privada vive en ``SRI_CREDENTIALS_PRIVATE_KEY``
  y solo se usa para abrir el sobre. Así se pueden atender varios RUC sin
  Secrets Manager y sin escribir contraseñas en el evento.

Nunca registre el evento completo: en el modo dinámico contiene el sobre, y en
el estático la configuración contiene la clave del portal.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .callback import ConfigCallback, Notificador
from .config import RobotConfig, RobotEntry, cargar_config
from .credentials import (
    CredencialesError,
    abrir_credenciales,
    claves_privadas_desde_entorno,
    max_edad_desde_entorno,
)
from .logging import obtener_logger
from .models import Periodo, TipoComprobante
from .pipeline import Opciones, Resumen, Robot
from .portal.selectors import Selectores
from .runtime import asegurar_runtime
from .storage import crear_almacenamiento

logger = obtener_logger(__name__)

CONFIG_POR_DEFECTO = "/opt/config/robots.yaml"
SELECTORES_POR_DEFECTO = "/opt/config/selectors.yaml"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Ejecuta el robot y notifica el resultado por callback firmado.

    El perfil de Chrome (y, en el despliegue ZIP, el navegador) se prepara de
    forma perezosa: solo cuando el pipeline detecta que hay descargas
    pendientes. Una invocación sin trabajo responde sin materializar el
    runtime.
    """
    inicio = datetime.now(tz=UTC)
    event_id = _event_id(event)
    notificador = _crear_notificador(event)

    periodos: tuple[Periodo, ...] = ()
    ambiente = ""
    resumen: Resumen | None = None
    error = ""
    respuesta: dict[str, Any] = {}

    try:
        sellada = _texto(event.get("sealed_credentials"))
        configuracion = _cargar_configuracion(validar_robots=not sellada)
        if sellada:
            configuracion = _aplicar_credenciales_selladas(configuracion, event, sellada)

        selectores = Selectores.cargar(os.environ.get("SRI_ROBOT_SELECTORS", SELECTORES_POR_DEFECTO))
        almacenamiento = crear_almacenamiento(configuracion)

        periodos = _periodos(event)
        tipos = tuple(TipoComprobante.desde_codigo(codigo) for codigo in event.get("tipos", []) or [])
        ambiente = configuracion.storage.ambiente

        robot = Robot(
            configuracion,
            selectores,
            almacenamiento,
            preparar_runtime=lambda: _preparar_entorno(configuracion),
        )
        resumen = robot.ejecutar(
            Opciones(
                periodos=periodos,
                tipos=tipos,
                ruc=_texto(event.get("ruc")),
                forzar=bool(event.get("forzar", False)),
            )
        )

        respuesta.update(
            {
                "ambiente": ambiente,
                "periodos": [periodo.clave for periodo in periodos],
                "ejecutadoEn": datetime.now(tz=UTC).isoformat(),
                **resumen.a_dict(),
            }
        )

        logger.info(
            "ejecución finalizada",
            extra={
                "ambiente": ambiente,
                "periodos": [periodo.clave for periodo in periodos],
                "fallos": len(resumen.fallos),
            },
        )
        return respuesta
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
        logger.exception("la ejecución terminó con error", extra={"event_id": event_id})
        raise
    finally:
        if notificador is not None:
            entrega = notificador.enviar(
                event_id,
                _cuerpo_callback(
                    event_id=event_id,
                    event=event,
                    context=context,
                    estado=_estado(resumen, error),
                    ambiente=ambiente,
                    periodos=periodos,
                    inicio=inicio,
                    resumen=resumen,
                    error=error,
                ),
                plazo_ms=_plazo_ms(context),
            )
            respuesta["callback"] = entrega.a_dict()


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


def _preparar_entorno(configuracion: RobotConfig) -> None:
    """Materializa los artefactos pesados justo antes de abrir Chrome.

    Se invoca desde el pipeline solo cuando hay descargas pendientes. En el
    despliegue ZIP descarga el navegador y el perfil desde S3; en el
    despliegue por imagen solo copia el perfil horneado en ``/opt``.
    """
    asegurar_runtime(configuracion)
    _preparar_perfil(configuracion)


def _crear_notificador(event: dict[str, Any]) -> Notificador | None:
    """Crea el notificador si el callback está configurado."""
    try:
        configuracion = ConfigCallback.desde_entorno(_texto(event.get("callback_url")))
    except ValueError as error:
        logger.error("callback mal configurado: se omite", extra={"error": str(error)})
        return None
    return Notificador(configuracion) if configuracion is not None else None


def _event_id(event: dict[str, Any]) -> str:
    """Devuelve el ``event_id`` del evento o genera uno estable por invocación."""
    crudo = _texto(event.get("event_id"))
    if crudo and len(crudo) <= 128 and crudo.isprintable():
        return crudo
    if crudo:
        logger.warning("event_id inválido: se genera uno nuevo", extra={"event_id": crudo[:40]})
    return f"sri-{uuid4().hex}"


def _estado(resumen: Resumen | None, error: str) -> str:
    """Resume el resultado en ``completed``, ``partial`` o ``failed``."""
    if error or resumen is None:
        return "failed"
    if not resumen.fallos:
        return "completed"
    if resumen.resultados or resumen.omitidos or resumen.sin_datos:
        return "partial"
    return "failed"


def _cuerpo_callback(
    *,
    event_id: str,
    event: dict[str, Any],
    context: Any,
    estado: str,
    ambiente: str,
    periodos: tuple[Periodo, ...],
    inicio: datetime,
    resumen: Resumen | None,
    error: str,
) -> dict[str, Any]:
    """Construye el sobre JSON del callback (versión 1)."""
    fin = datetime.now(tz=UTC)
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "sri.robot.finished",
        "status": estado,
        "ambiente": ambiente,
        "ruc": _texto(event.get("ruc")) or _primer_ruc(resumen),
        "periodos": [periodo.clave for periodo in periodos],
        "request_id": getattr(context, "aws_request_id", None),
        "started_at": inicio.isoformat(),
        "finished_at": fin.isoformat(),
        "duration_ms": int((fin - inicio).total_seconds() * 1000),
        "result": resumen.a_dict() if resumen is not None else None,
        "error": error or None,
    }


def _primer_ruc(resumen: Resumen | None) -> str:
    if resumen is not None:
        for resultado in resumen.resultados:
            return resultado.ruc
    return ""


def _plazo_ms(context: Any) -> int | None:
    """Tiempo restante de la invocación, si el contexto lo expone."""
    obtener = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(obtener):
        return None
    try:
        return int(obtener())
    except Exception:  # noqa: BLE001 - un contexto incompleto no debe romper el callback
        return None


def _aplicar_credenciales_selladas(
    configuracion: RobotConfig,
    event: dict[str, Any],
    sellada: str,
) -> RobotConfig:
    """Sustituye los contribuyentes del YAML por el del sobre cifrado.

    El RUC exterior se usa para la ruta S3 y los logs; el interior se valida
    contra él para evitar que un evento mezcle las credenciales de un
    contribuyente con el RUC de otro.
    """
    credenciales = abrir_credenciales(
        sellada,
        claves_privadas_desde_entorno(),
        max_age_seconds=max_edad_desde_entorno(),
    )

    ruc_evento = _texto(event.get("ruc"))
    if ruc_evento and ruc_evento != credenciales.ruc:
        raise CredencialesError("El RUC del evento no coincide con el RUC del sobre de credenciales.")

    entrada = RobotEntry(
        ruc=credenciales.ruc,
        password=credenciales.password,
        clave_adicional=credenciales.clave_adicional,
        activo=True,
    )
    logger.info("credenciales dinámicas aceptadas", extra={"ruc": credenciales.ruc})
    return configuracion.con_robot(entrada)


def _cargar_configuracion(*, validar_robots: bool = True) -> RobotConfig:
    """Carga la configuración; opcionalmente valida las claves estáticas.

    En el modo dinámico no se valida ``robots`` porque las credenciales llegan
    en el evento y el YAML puede tener ``robots: []``.
    """
    ruta = os.environ.get("SRI_ROBOT_CONFIG", CONFIG_POR_DEFECTO)
    configuracion = cargar_config(ruta)

    if validar_robots:
        _validar_robots(configuracion)

    return configuracion


def _validar_robots(configuracion: RobotConfig) -> None:
    """Comprueba que cada contribuyente activo tenga credenciales resolubles."""
    for entrada in configuracion.robots:
        if entrada.activo:
            entrada.a_config_ruc()


def _texto(valor: Any) -> str:
    """Normaliza un valor opcional del evento a texto sin espacios."""
    return str(valor).strip() if valor is not None else ""


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

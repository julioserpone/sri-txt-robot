"""Materialización del runtime pesado en el despliegue ZIP.

El navegador no cabe en el paquete de la función (Lambda limita el ZIP a
250 MB sin comprimir). Por eso vive en S3 y se extrae en ``/tmp`` solo cuando
la invocación tiene descargas pendientes:

- ``SRI_RUNTIME_BROWSER_KEY`` apunta al bundle (Chromium + Xvfb + libs + fuentes).
- ``SRI_RUNTIME_PERFIL_KEY`` apunta al perfil de Chrome ya calentado.

Ambos objetos deben estar en un bucket privado y se verifican con SHA-256
antes de extraerlos. Si las variables no están definidas, el módulo no hace
nada: es el caso del despliegue por imagen de contenedor.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

from .config import RobotConfig
from .logging import obtener_logger

logger = obtener_logger(__name__)

BUCKET_ENV = "SRI_RUNTIME_BUCKET"
PREFIX_ENV = "SRI_RUNTIME_PREFIX"
DIR_ENV = "SRI_RUNTIME_DIR"
BROWSER_KEY_ENV = "SRI_RUNTIME_BROWSER_KEY"
BROWSER_SHA_ENV = "SRI_RUNTIME_BROWSER_SHA256"
PERFIL_KEY_ENV = "SRI_RUNTIME_PERFIL_KEY"
PERFIL_SHA_ENV = "SRI_RUNTIME_PERFIL_SHA256"

DIR_POR_DEFECTO = "/tmp/sri-runtime"

#: Directorio donde el Xvfb del bundle busca ``xkbcomp`` (ruta parcheada en el
#: build; no puede ser /usr/bin porque en Lambda es de solo lectura).
RUTA_XKBCOMP = Path("/tmp/xkb")

_BLOQUE = 1024 * 1024


def runtime_configurado() -> bool:
    """Indica si el despliegue espera descargar el navegador desde S3."""
    return bool(os.environ.get(BROWSER_KEY_ENV, "").strip())


def asegurar_runtime(configuracion: RobotConfig) -> None:
    """Descarga y configura el navegador y el perfil, una sola vez por sandbox."""
    if not runtime_configurado():
        return

    raiz = Path(os.environ.get(DIR_ENV, DIR_POR_DEFECTO)).expanduser()
    _asegurar_bundle(raiz)
    _asegurar_perfil(configuracion)
    _configurar_entorno(raiz)


def _asegurar_bundle(raiz: Path) -> None:
    if (raiz / "version.json").is_file():
        logger.info("bundle del navegador ya presente en /tmp", extra={"raiz": str(raiz)})
        return

    key = _variable(BROWSER_KEY_ENV)
    sha = _variable(BROWSER_SHA_ENV)
    if not key or not sha:
        raise RuntimeError(f"Faltan {BROWSER_KEY_ENV} o {BROWSER_SHA_ENV}: no se puede preparar el navegador.")

    inicio = time.monotonic()
    temporal = raiz.with_name(f"{raiz.name}-tmp")
    archivo = Path("/tmp") / f"{raiz.name}-{os.getpid()}.tar.gz"
    shutil.rmtree(temporal, ignore_errors=True)

    try:
        _descargar(key, archivo, sha)
        temporal.mkdir(parents=True)
        _extraer(archivo, temporal)
        if not (temporal / "version.json").is_file():
            raise RuntimeError("El bundle descargado no contiene version.json; se aborta la extracción.")
        shutil.rmtree(raiz, ignore_errors=True)
        temporal.rename(raiz)
    except Exception:
        shutil.rmtree(temporal, ignore_errors=True)
        raise
    finally:
        archivo.unlink(missing_ok=True)

    logger.info(
        "bundle del navegador listo",
        extra={"raiz": str(raiz), "segundos": round(time.monotonic() - inicio, 2)},
    )


def _asegurar_perfil(configuracion: RobotConfig) -> None:
    key = _variable(PERFIL_KEY_ENV)
    if not key:
        return

    sha = _variable(PERFIL_SHA_ENV)
    if not sha:
        raise RuntimeError(f"Falta {PERFIL_SHA_ENV}: no se puede verificar el perfil de Chrome.")

    destino_texto = configuracion.portal.perfil_dir.strip()
    if not destino_texto:
        raise RuntimeError("El perfil remoto requiere portal.perfil_dir en la configuración.")

    destino = Path(destino_texto).expanduser()
    if destino.exists():
        logger.info("perfil de Chrome ya presente en /tmp", extra={"perfil": str(destino)})
        return

    inicio = time.monotonic()
    archivo = Path("/tmp") / f"perfil-{os.getpid()}.tar.gz"
    temporal = destino.with_name(f"{destino.name}-tmp")
    shutil.rmtree(temporal, ignore_errors=True)

    try:
        _descargar(key, archivo, sha)
        temporal.mkdir(parents=True)
        _extraer(archivo, temporal)
        temporal.rename(destino)
    except Exception:
        shutil.rmtree(temporal, ignore_errors=True)
        raise
    finally:
        archivo.unlink(missing_ok=True)

    logger.info(
        "perfil de Chrome listo",
        extra={"perfil": str(destino), "segundos": round(time.monotonic() - inicio, 2)},
    )


def _descargar(key: str, destino: Path, sha_esperado: str) -> None:
    """Descarga un objeto de S3 y verifica su SHA-256."""
    bucket = _bucket()
    inicio = time.monotonic()
    logger.info("descargando artefacto del runtime", extra={"bucket": bucket, "key": key})

    cliente = _cliente_s3()
    cliente.download_file(bucket, key, str(destino))

    obtenido = _sha256(destino)
    if obtenido != sha_esperado.strip().lower():
        raise RuntimeError(f"SHA-256 no coincide para s3://{bucket}/{key}: {obtenido} != {sha_esperado}.")

    logger.info(
        "artefacto descargado y verificado",
        extra={
            "key": key,
            "bytes": destino.stat().st_size,
            "segundos": round(time.monotonic() - inicio, 2),
        },
    )


def _extraer(archivo: Path, destino: Path) -> None:
    with tarfile.open(archivo, "r:gz") as tar:
        tar.extractall(destino, filter="data")  # noqa: S202 - el bundle es propio y va firmado por SHA-256


def _configurar_entorno(raiz: Path) -> None:
    """Ajusta las variables que Playwright, Chrome y Xvfb leen al arrancar."""
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(raiz / "ms-playwright")
    os.environ["PATH"] = _prepender(str(raiz / "bin"), os.environ.get("PATH", ""))
    os.environ["LD_LIBRARY_PATH"] = _prepender(str(raiz / "lib"), os.environ.get("LD_LIBRARY_PATH", ""))

    os.environ["HOME"] = "/tmp"
    os.environ["XDG_CACHE_HOME"] = "/tmp/.cache"
    Path("/tmp/.cache").mkdir(parents=True, exist_ok=True)
    Path("/tmp/.X11-unix").mkdir(parents=True, exist_ok=True)

    fuentes = raiz / "fonts"
    if fuentes.is_dir():
        conf = raiz / "fonts.conf"
        if not conf.is_file():
            conf.write_text(
                '<?xml version="1.0"?>\n'
                '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                "<fontconfig>\n"
                f"  <dir>{fuentes}</dir>\n"
                '  <cachedir>/tmp/.cache/fontconfig</cachedir>\n'
                "</fontconfig>\n",
                encoding="utf-8",
            )
        os.environ["FONTCONFIG_FILE"] = str(conf)

    xkb = raiz / "xkb"
    if xkb.is_dir():
        os.environ["SRI_XKB_DIR"] = str(xkb)
    _enlazar_xkbcomp(raiz)


def _enlazar_xkbcomp(raiz: Path) -> None:
    """Publica ``xkbcomp`` en la ruta que busca el Xvfb del bundle.

    El binario de Xvfb se compila con ``/usr/bin`` como directorio de
    herramientas XKB. En Lambda ese directorio es de solo lectura, así que el
    bundle lo redirige a ``/tmp/xkb`` y aquí se enlaza el ``xkbcomp`` incluido.
    """
    binario = raiz / "bin" / "xkbcomp"
    if not binario.is_file():
        return

    destino = RUTA_XKBCOMP
    destino.mkdir(parents=True, exist_ok=True)
    enlace = destino / "xkbcomp"
    if enlace.is_symlink() or enlace.exists():
        enlace.unlink()
    enlace.symlink_to(binario)


def _bucket() -> str:
    return _variable(BUCKET_ENV) or _variable("SRI_BUCKET")


def _cliente_s3() -> Any:
    import boto3  # importación perezosa: el CLI local no necesita boto3 para este módulo

    return boto3.client("s3")


def _sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(_BLOQUE), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _prepender(valor: str, actual: str) -> str:
    return f"{valor}:{actual}" if actual else valor


def _variable(nombre: str) -> str:
    return os.environ.get(nombre, "").strip()

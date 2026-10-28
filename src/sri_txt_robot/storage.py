"""Persistencia de los TXT descargados y de sus manifiestos.

Dos implementaciones: disco local (desarrollo y pruebas) y Amazon S3 (producción
en Lambda). La clave la construye :mod:`sri_txt_robot.manifest` y sigue el
patrón::

    {prefijo}/{ambiente}/{ruc}/{periodo}/{tipo}-recibidas-{periodo}.txt
    {prefijo}/{ambiente}/{ruc}/{periodo}/{tipo}-recibidas-{periodo}-claves.json
    {prefijo}/{ambiente}/{ruc}/{periodo}/manifest.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .config import ConfigError, RobotConfig
from .logging import obtener_logger

logger = obtener_logger(__name__)


class Almacenamiento(Protocol):
    """Contrato de persistencia de resultados."""

    def guardar_txt(self, clave: str, contenido: bytes) -> str:
        """Guarda el TXT y devuelve la clave lógica del objeto."""
        ...

    def guardar_json(self, clave: str, datos: dict[str, Any]) -> str:
        """Guarda un documento JSON y devuelve la clave lógica del objeto."""
        ...

    def guardar_xml(self, clave: str, contenido: bytes) -> str:
        """Guarda un archivo XML y devuelve la clave lógica del objeto."""
        ...

    def existe(self, clave: str) -> bool:
        """Indica si el objeto ya fue guardado."""
        ...


class AlmacenamientoLocal:
    """Guarda los resultados en el sistema de archivos."""

    def __init__(self, directorio: str | Path) -> None:
        self._directorio = Path(directorio)

    def guardar_txt(self, clave: str, contenido: bytes) -> str:
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        ruta.chmod(0o600)
        logger.info("TXT guardado", extra={"clave": clave, "bytes": len(contenido)})
        return clave

    def guardar_json(self, clave: str, datos: dict[str, Any]) -> str:
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        ruta.chmod(0o600)
        logger.info("manifiesto guardado", extra={"clave": clave})
        return clave

    def guardar_xml(self, clave: str, contenido: bytes) -> str:
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        ruta.chmod(0o600)
        logger.info("XML guardado", extra={"clave": clave, "bytes": len(contenido)})
        return clave

    def existe(self, clave: str) -> bool:
        return self._ruta(clave).is_file()

    def _ruta(self, clave: str) -> Path:
        return self._directorio / clave


class AlmacenamientoS3:
    """Guarda los resultados en un bucket de Amazon S3."""

    def __init__(self, bucket: str, prefix: str = "", cliente: Any | None = None) -> None:
        if not bucket:
            raise ValueError("El bucket de S3 es obligatorio.")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        if cliente is None:
            import boto3  # Importación diferida: solo se necesita en AWS.

            cliente = boto3.client("s3")
        self._cliente = cliente

    def guardar_txt(self, clave: str, contenido: bytes) -> str:
        destino = self._destino(clave)
        self._cliente.put_object(
            Bucket=self._bucket,
            Key=destino,
            Body=contenido,
            ContentType="text/plain; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        logger.info("TXT guardado en S3", extra={"clave": clave, "bytes": len(contenido)})
        return clave

    def guardar_json(self, clave: str, datos: dict[str, Any]) -> str:
        destino = self._destino(clave)
        cuerpo = json.dumps(datos, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self._cliente.put_object(
            Bucket=self._bucket,
            Key=destino,
            Body=cuerpo,
            ContentType="application/json; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        logger.info("manifiesto guardado en S3", extra={"clave": clave})
        return clave

    def guardar_xml(self, clave: str, contenido: bytes) -> str:
        destino = self._destino(clave)
        self._cliente.put_object(
            Bucket=self._bucket,
            Key=destino,
            Body=contenido,
            ContentType="application/xml; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        logger.info("XML guardado en S3", extra={"clave": clave, "bytes": len(contenido)})
        return clave

    def existe(self, clave: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._cliente.head_object(Bucket=self._bucket, Key=self._destino(clave))
        except ClientError:
            return False
        return True

    def _destino(self, clave: str) -> str:
        return f"{self._prefix}/{clave.lstrip('/')}" if self._prefix else clave.lstrip("/")


def crear_almacenamiento(configuracion: RobotConfig) -> Almacenamiento:
    """Construye el almacenamiento según la configuración."""
    if configuracion.storage.bucket:
        return AlmacenamientoS3(configuracion.storage.bucket, configuracion.storage.prefix)
    if configuracion.storage.local_dir:
        return AlmacenamientoLocal(configuracion.storage.local_dir)
    raise ConfigError("Configure 'storage.bucket' o 'storage.local_dir'.")

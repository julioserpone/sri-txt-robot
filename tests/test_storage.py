"""Persistencia local y en S3 con el árbol ambiente/ruc/período."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sri_txt_robot.manifest import nombre_claves, nombre_txt
from sri_txt_robot.models import TipoComprobante
from sri_txt_robot.storage import AlmacenamientoLocal, AlmacenamientoS3

CLAVE = nombre_txt("1790012345001", "2026-08", TipoComprobante.FACTURA, ambiente="produccion")


class ClienteFalso:
    """Doble del cliente de S3 que solo recuerda las llamadas."""

    def __init__(self) -> None:
        self.llamadas: list[dict[str, Any]] = []

    def put_object(self, **parametros: Any) -> None:
        self.llamadas.append(parametros)


def test_s3_guarda_bajo_ambiente_ruc_y_periodo() -> None:
    cliente = ClienteFalso()
    almacen = AlmacenamientoS3("mi-bucket", cliente=cliente)

    almacen.guardar_txt(CLAVE, b"contenido")

    assert cliente.llamadas[0]["Key"] == ("produccion/1790012345001/2026-08/facturas-recibidas-2026-08.txt")
    assert cliente.llamadas[0]["Bucket"] == "mi-bucket"
    assert cliente.llamadas[0]["ServerSideEncryption"] == "AES256"


def test_s3_respeta_el_prefijo_opcional() -> None:
    cliente = ClienteFalso()
    almacen = AlmacenamientoS3("mi-bucket", "/robot/", cliente=cliente)

    almacen.guardar_json(nombre_claves("1790012345001", "2026-08", TipoComprobante.RETENCION, ambiente="test"), {})

    assert cliente.llamadas[0]["Key"] == ("robot/test/1790012345001/2026-08/retenciones-recibidas-2026-08-claves.json")


def test_local_escribe_el_mismo_arbol(tmp_path: Path) -> None:
    almacen = AlmacenamientoLocal(tmp_path)

    almacen.guardar_txt(CLAVE, b"contenido")
    almacen.guardar_json(
        nombre_claves("1790012345001", "2026-08", TipoComprobante.FACTURA, ambiente="produccion"), {"total": 0}
    )

    ruta = tmp_path / CLAVE
    assert ruta.read_bytes() == b"contenido"
    assert almacen.existe(CLAVE)
    claves = json.loads((ruta.parent / "facturas-recibidas-2026-08-claves.json").read_text(encoding="utf-8"))
    assert claves["total"] == 0

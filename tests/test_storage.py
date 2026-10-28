"""Persistencia local y en S3 con el árbol ambiente/ruc/período."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

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


class ClienteLecturaFalsa:
    """Doble del cliente de S3 que solo responde a get_object."""

    def __init__(self, objetos: dict[str, str]) -> None:
        self._objetos = objetos

    def get_object(self, **parametros: Any) -> dict[str, Any]:
        clave = parametros["Key"]
        if clave not in self._objetos:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self._objetos[clave].encode("utf-8"))}


def test_local_leer_json_devuelve_none_si_no_existe(tmp_path: Path) -> None:
    assert AlmacenamientoLocal(tmp_path).leer_json(CLAVE) is None


def test_local_leer_json_devuelve_lo_guardado(tmp_path: Path) -> None:
    almacen = AlmacenamientoLocal(tmp_path)
    almacen.guardar_json(CLAVE, {"total": 3})

    assert almacen.leer_json(CLAVE) == {"total": 3}


def test_local_leer_json_ignora_un_archivo_corrupto(tmp_path: Path) -> None:
    ruta = tmp_path / CLAVE
    ruta.parent.mkdir(parents=True)
    ruta.write_text("{no es json", encoding="utf-8")

    assert AlmacenamientoLocal(tmp_path).leer_json(CLAVE) is None


def test_s3_leer_json_devuelve_none_si_no_existe() -> None:
    almacen = AlmacenamientoS3("mi-bucket", cliente=ClienteLecturaFalsa({}))

    assert almacen.leer_json(CLAVE) is None


def test_s3_leer_json_aplica_el_prefijo() -> None:
    cliente = ClienteLecturaFalsa({f"robot/{CLAVE}": '{"total": 1}'})
    almacen = AlmacenamientoS3("mi-bucket", "/robot/", cliente=cliente)

    assert almacen.leer_json(CLAVE) == {"total": 1}

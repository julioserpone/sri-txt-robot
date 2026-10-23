"""Preparación del perfil de Chrome dentro de Lambda."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sri_txt_robot import handler
from sri_txt_robot.config import PortalConfig, RobotConfig
from sri_txt_robot.credentials import (
    CredencialesError,
    generar_par_claves,
    sellar_credenciales,
)
from sri_txt_robot.pipeline import Resumen

RUC = "1790012345001"
CLAVE = "clave-sellada-de-prueba"


def _configuracion(destino: Path) -> RobotConfig:
    return RobotConfig(portal=PortalConfig(perfil_dir=str(destino)))


def test_preparar_perfil_copia_el_perfil_de_la_imagen(tmp_path: Path, monkeypatch) -> None:
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "Cookies").write_text("datos", encoding="utf-8")
    monkeypatch.setenv("SRI_PERFIL_ORIGEN", str(origen))
    destino = tmp_path / "destino"

    handler._preparar_perfil(_configuracion(destino))

    assert (destino / "Cookies").read_text(encoding="utf-8") == "datos"
    assert not (tmp_path / "destino-tmp").exists()


def test_preparar_perfil_respeta_un_perfil_ya_copiado(tmp_path: Path, monkeypatch) -> None:
    origen = tmp_path / "origen"
    origen.mkdir()
    monkeypatch.setenv("SRI_PERFIL_ORIGEN", str(origen))
    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "Cookies").write_text("anterior", encoding="utf-8")

    handler._preparar_perfil(_configuracion(destino))

    assert (destino / "Cookies").read_text(encoding="utf-8") == "anterior"


def test_preparar_perfil_sin_origen_no_hace_nada(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SRI_PERFIL_ORIGEN", raising=False)
    destino = tmp_path / "destino"

    handler._preparar_perfil(_configuracion(destino))

    assert not destino.exists()


class _RobotFalso:
    """Sustituye al robot real para no abrir Chrome en las pruebas."""

    instancias: list[_RobotFalso] = []

    def __init__(self, configuracion: RobotConfig, selectores: Any, almacenamiento: Any) -> None:
        self.configuracion = configuracion
        self.selectores = selectores
        self.almacenamiento = almacenamiento
        self.opciones: Any = None
        _RobotFalso.instancias.append(self)

    def ejecutar(self, opciones: Any) -> Resumen:
        self.opciones = opciones
        return Resumen()


class _SelectoresFalso:
    @staticmethod
    def cargar(_ruta: str) -> object:
        return object()


def _instalar_dobles(monkeypatch: pytest.MonkeyPatch) -> None:
    _RobotFalso.instancias.clear()
    monkeypatch.setattr(handler, "Robot", _RobotFalso)
    monkeypatch.setattr(handler, "Selectores", _SelectoresFalso)
    monkeypatch.setattr(handler, "crear_almacenamiento", lambda _configuracion: object())
    monkeypatch.setattr(handler, "_preparar_perfil", lambda _configuracion: None)


def _escribir_configuracion(tmp_path: Path, robots: str) -> Path:
    ruta = tmp_path / "robots.yaml"
    ruta.write_text(
        f"""
version: 1
storage:
  local_dir: "./datos"
{robots}
""",
        encoding="utf-8",
    )
    return ruta


def test_handler_usa_las_credenciales_selladas_del_evento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instalar_dobles(monkeypatch)
    ruta = _escribir_configuracion(tmp_path, "robots: []")
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "0")

    publica, privada = generar_par_claves()
    monkeypatch.setenv("SRI_CREDENTIALS_PRIVATE_KEY", privada)
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    respuesta = handler.handler(
        {"periodo": "2026-09", "ruc": RUC, "sealed_credentials": sellada},
        None,
    )

    robot = _RobotFalso.instancias[-1]
    entrada = robot.configuracion.robots[0]
    assert entrada.ruc == RUC
    assert entrada.a_config_ruc().credenciales.password == CLAVE
    assert robot.configuracion.storage.local_dir == "./datos"
    assert robot.opciones.ruc == RUC
    assert robot.opciones.periodos[0].clave == "2026-09"
    assert CLAVE not in json.dumps(respuesta)


def test_handler_acepta_el_ruc_del_sobre_si_el_evento_no_lo_trae(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instalar_dobles(monkeypatch)
    ruta = _escribir_configuracion(tmp_path, "robots: []")
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "0")

    publica, privada = generar_par_claves()
    monkeypatch.setenv("SRI_CREDENTIALS_PRIVATE_KEY", privada)
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    handler.handler({"periodo": "2026-09", "sealed_credentials": sellada}, None)

    robot = _RobotFalso.instancias[-1]
    assert robot.configuracion.robots[0].ruc == RUC
    assert robot.opciones.ruc == ""


def test_handler_rechaza_un_ruc_exterior_distinto_al_sellado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instalar_dobles(monkeypatch)
    ruta = _escribir_configuracion(tmp_path, "robots: []")
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "0")

    publica, privada = generar_par_claves()
    monkeypatch.setenv("SRI_CREDENTIALS_PRIVATE_KEY", privada)
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    with pytest.raises(CredencialesError, match="no coincide"):
        handler.handler(
            {"periodo": "2026-09", "ruc": "1758871873001", "sealed_credentials": sellada},
            None,
        )


def test_handler_falla_si_falta_la_clave_privada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instalar_dobles(monkeypatch)
    ruta = _escribir_configuracion(tmp_path, "robots: []")
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.delenv("SRI_CREDENTIALS_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SRI_CREDENTIALS_PRIVATE_KEY_ANTERIOR", raising=False)

    publica, _privada = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    with pytest.raises(CredencialesError, match="Falta la clave privada"):
        handler.handler({"periodo": "2026-09", "sealed_credentials": sellada}, None)


def test_handler_sigue_usando_el_modo_estatico_sin_sobre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instalar_dobles(monkeypatch)
    ruta = _escribir_configuracion(
        tmp_path,
        f"""
robots:
  - ruc: "{RUC}"
    password_env: "SRI_PASSWORD_PRUEBA"
""",
    )
    monkeypatch.setenv("SRI_ROBOT_CONFIG", str(ruta))
    monkeypatch.setenv("SRI_PASSWORD_PRUEBA", CLAVE)

    handler.handler({"periodo": "2026-09", "ruc": RUC}, None)

    robot = _RobotFalso.instancias[-1]
    assert robot.configuracion.robots[0].ruc == RUC
    assert robot.configuracion.robots[0].a_config_ruc().credenciales.password == CLAVE

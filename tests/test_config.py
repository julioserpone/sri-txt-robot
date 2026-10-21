"""Pruebas de la carga de configuración."""

from __future__ import annotations

from pathlib import Path

import pytest

from sri_txt_robot.config import ConfigError, cargar_config
from sri_txt_robot.models import TipoComprobante


def _escribir(base: Path, contenido: str) -> Path:
    ruta = base / "robots.yaml"
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def test_carga_un_robot_con_credenciales_por_variable_de_entorno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SRI_PASSWORD_1790012345001", "secreto")
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1790012345001"
    password_env: "SRI_PASSWORD_1790012345001"
    clave_adicional: "1"
    tipos: ["01", "08"]
""",
    )

    configuracion = cargar_config(ruta)

    rucs = configuracion.rucs_activos()
    assert len(rucs) == 1
    assert rucs[0].credenciales.password == "secreto"
    assert rucs[0].credenciales.requiere_clave_adicional is True
    assert rucs[0].tipos == (TipoComprobante.FACTURA, TipoComprobante.RETENCION)


def test_expande_las_variables_de_entorno_en_el_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAVE_DE_PRUEBA", "valor-secreto")
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1234567890"
    password: "${CLAVE_DE_PRUEBA}"
""",
    )

    configuracion = cargar_config(ruta)

    assert configuracion.rucs_activos()[0].credenciales.password == "valor-secreto"


def test_falla_si_una_variable_de_entorno_no_esta_definida(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1790012345001"
    password: "${VARIABLE_QUE_NO_EXISTE_12345}"
""",
    )

    with pytest.raises(ConfigError, match="VARIABLE_QUE_NO_EXISTE_12345"):
        cargar_config(ruta)


def test_falla_si_el_robot_no_tiene_credenciales(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1790012345001"
""",
    )

    configuracion = cargar_config(ruta)

    with pytest.raises(ConfigError, match="password"):
        configuracion.rucs_activos()


def test_falla_si_el_archivo_no_existe(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No existe"):
        cargar_config(tmp_path / "inexistente.yaml")


def test_rechaza_un_ruc_invalido(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "12345"
    password: "x"
""",
    )

    with pytest.raises(Exception, match="1790012345001|pattern"):
        cargar_config(ruta)


def test_ignora_los_robots_inactivos(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1790012345001"
    password: "x"
    activo: false
""",
    )

    configuracion = cargar_config(ruta)

    assert configuracion.rucs_activos() == []


def test_la_clave_de_anticaptcha_se_lee_del_entorno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTICAPTCHA_API_KEY", "clave-de-prueba")
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots: []
""",
    )

    configuracion = cargar_config(ruta)

    assert configuracion.captcha.resolver_api_key() == "clave-de-prueba"


def test_falla_si_no_hay_clave_de_anticaptcha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots: []
""",
    )

    configuracion = cargar_config(ruta)

    with pytest.raises(ConfigError, match="ANTICAPTCHA_API_KEY"):
        configuracion.captcha.resolver_api_key()

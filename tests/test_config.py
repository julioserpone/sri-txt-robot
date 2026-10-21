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
    tipos: ["01", "07"]
""",
    )

    configuracion = cargar_config(ruta)

    rucs = configuracion.rucs_activos()
    assert len(rucs) == 1
    assert rucs[0].credenciales.password == "secreto"
    assert rucs[0].credenciales.requiere_clave_adicional is True
    assert rucs[0].tipos == (TipoComprobante.FACTURA, TipoComprobante.RETENCION)


def test_expande_las_variables_de_entorno_en_el_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_la_clave_de_anticaptcha_se_lee_del_entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_falla_si_no_hay_clave_de_anticaptcha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_los_comentarios_no_expanden_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La plantilla menciona ${NOMBRE} en un comentario: copiarla debe funcionar."""
    monkeypatch.delenv("NOMBRE", raising=False)
    ruta = _escribir(
        tmp_path,
        """
version: 1
# Los secretos se pasan como ${NOMBRE} o por variables de entorno.
storage:
  local_dir: "./datos"
robots: []
""",
    )

    configuracion = cargar_config(ruta)

    assert configuracion.storage.local_dir == "./datos"


def test_expande_variables_en_los_valores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MI_CLAVE_SRI", "secreto")
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots:
  - ruc: "1790012345001"
    password: "${MI_CLAVE_SRI}"
""",
    )

    configuracion = cargar_config(ruta)

    assert configuracion.robots[0].a_config_ruc().credenciales.password == "secreto"


def test_falla_si_la_variable_del_valor_no_existe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_DEFINIDA", raising=False)
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "${NO_DEFINIDA}"
robots: []
""",
    )

    with pytest.raises(ConfigError, match="NO_DEFINIDA"):
        cargar_config(ruta)


def test_el_ambiente_por_defecto_es_dev(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots: []
""",
    )

    assert cargar_config(ruta).storage.ambiente == "dev"


def test_sri_ambiente_sobrescribe_el_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRI_AMBIENTE", "Produccion")
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
robots: []
""",
    )

    assert cargar_config(ruta).storage.ambiente == "produccion"


def test_sri_bucket_fuerza_el_almacenamiento_en_s3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRI_BUCKET", "mi-bucket")
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

    assert configuracion.storage.bucket == "mi-bucket"
    assert configuracion.storage.local_dir == ""


def test_un_ambiente_invalido_se_rechaza(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
version: 1
storage:
  local_dir: "./datos"
  ambiente: "dos palabras"
robots: []
""",
    )

    with pytest.raises(Exception, match="Ambiente inválido"):
        cargar_config(ruta)

"""Credenciales y overrides por línea de comandos."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from sri_txt_robot.cli import _configuracion_para, _pedir_password
from sri_txt_robot.config import ConfigError


def _sin_archivo(tmp_path: Path) -> Path:
    return tmp_path / "no-existe.yaml"


def test_sin_archivo_todo_viene_del_comando(tmp_path: Path) -> None:
    configuracion = _configuracion_para(
        _sin_archivo(tmp_path),
        ruc="1758871873001",
        password="clave-secreta",
        api_key="clave-anticaptcha",
        perfil="~/.sri-txt-robot/perfil",
        salida=None,
        visible=True,
    )

    assert [entrada.ruc for entrada in configuracion.robots] == ["1758871873001"]
    assert configuracion.robots[0].a_config_ruc().credenciales.password == "clave-secreta"
    assert configuracion.portal.perfil_dir == "~/.sri-txt-robot/perfil"
    assert configuracion.portal.headless is False
    assert configuracion.storage.local_dir == "./datos"
    assert configuracion.captcha.resolver_api_key() == "clave-anticaptcha"


def test_password_exige_ruc(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--ruc"):
        _configuracion_para(
            _sin_archivo(tmp_path),
            ruc=None,
            password="clave",
            api_key=None,
            perfil=None,
            salida=None,
            visible=False,
        )


def test_ruc_desconocido_sin_password_falla(tmp_path: Path) -> None:
    ruta = tmp_path / "robots.yaml"
    ruta.write_text(
        """
version: 1
robots:
  - ruc: "1790012345001"
    password: "otra"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="no está en"):
        _configuracion_para(
            ruta,
            ruc="1758871873001",
            password=None,
            api_key=None,
            perfil=None,
            salida=None,
            visible=False,
        )


def test_sin_contribuyentes_falla(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No hay contribuyentes"):
        _configuracion_para(
            _sin_archivo(tmp_path),
            ruc=None,
            password=None,
            api_key=None,
            perfil=None,
            salida=None,
            visible=False,
        )


def test_pedir_password_respeta_el_valor_dado() -> None:
    assert _pedir_password("1758871873001", "dada") == "dada"
    assert _pedir_password(None, None) is None


def test_pedir_password_sin_terminal_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(typer.BadParameter):
        _pedir_password("1758871873001", None)


def test_el_ambiente_se_pasa_por_el_comando(tmp_path: Path) -> None:
    configuracion = _configuracion_para(
        _sin_archivo(tmp_path),
        ruc="1758871873001",
        password="clave",
        api_key=None,
        perfil=None,
        salida=None,
        ambiente="Test",
        visible=False,
    )

    assert configuracion.storage.ambiente == "test"


def test_un_ambiente_invalido_se_rechaza(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Ambiente inválido"):
        _configuracion_para(
            _sin_archivo(tmp_path),
            ruc="1758871873001",
            password="clave",
            api_key=None,
            perfil=None,
            salida=None,
            ambiente="con espacios",
            visible=False,
        )

"""Perfil dedicado de Chrome: configuración, utilidades y comando ``calentar``."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from sri_txt_robot.cli import app
from sri_txt_robot.config import PortalConfig
from sri_txt_robot.portal.session import Navegador, _esperar_cdp, _limpiar_bloqueos, _puerto_libre


def test_portal_por_defecto_no_usa_perfil() -> None:
    portal = PortalConfig()
    assert portal.perfil_dir == ""
    assert portal.chrome_path == ""
    assert Navegador(portal).usa_perfil is False


def test_portal_con_perfil_activa_modo_cdp() -> None:
    portal = PortalConfig(perfil_dir="/tmp/perfil-sri", chrome_path="/usr/bin/chrome")
    assert Navegador(portal).usa_perfil is True


def test_puerto_libre_esta_disponible() -> None:
    puerto = _puerto_libre()
    assert 1024 < puerto < 65536

    # Si el puerto devuelto sigue libre, se puede volver a enlazar.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conexion:
        conexion.bind(("127.0.0.1", puerto))


class _ProcesoTerminado:
    """Doble de ``Popen`` para el camino de error de ``_esperar_cdp``."""

    returncode = 3

    def poll(self) -> int:
        return 3


def test_esperar_cdp_falla_si_chrome_termino() -> None:
    proceso = cast(subprocess.Popen[bytes], _ProcesoTerminado())
    with pytest.raises(RuntimeError, match="terminó antes"):
        _esperar_cdp(1, proceso)


def test_limpiar_bloqueos_retira_solo_los_enlaces_de_chrome(tmp_path: Path) -> None:
    for nombre in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (tmp_path / nombre).symlink_to("/tmp/equipo-anterior")
    (tmp_path / "Cookies").write_text("datos", encoding="utf-8")

    _limpiar_bloqueos(tmp_path)

    assert not list(tmp_path.glob("Singleton*"))
    assert (tmp_path / "Cookies").read_text(encoding="utf-8") == "datos"


def test_calentar_exige_perfil(tmp_path: Path) -> None:
    configuracion = tmp_path / "robots.yaml"
    configuracion.write_text(
        """
version: 1
portal:
  headless: false
robots:
  - ruc: "1790012345001"
    password_env: "SRI_PASSWORD"
""",
        encoding="utf-8",
    )

    resultado = CliRunner().invoke(app, ["calentar", "-c", str(configuracion)])

    assert resultado.exit_code == 2
    assert "perfil_dir" in resultado.output


def test_pantalla_viva_es_falsa_para_una_pantalla_inexistente() -> None:
    from sri_txt_robot.portal.session import _pantalla_viva

    assert _pantalla_viva(":sri-inexistente") is False

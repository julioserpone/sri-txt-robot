"""Preparación del perfil de Chrome dentro de Lambda."""

from __future__ import annotations

from pathlib import Path

from sri_txt_robot.config import PortalConfig, RobotConfig
from sri_txt_robot.handler import _preparar_perfil


def _configuracion(destino: Path) -> RobotConfig:
    return RobotConfig(portal=PortalConfig(perfil_dir=str(destino)))


def test_preparar_perfil_copia_el_perfil_de_la_imagen(tmp_path: Path, monkeypatch) -> None:
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "Cookies").write_text("datos", encoding="utf-8")
    monkeypatch.setenv("SRI_PERFIL_ORIGEN", str(origen))
    destino = tmp_path / "destino"

    _preparar_perfil(_configuracion(destino))

    assert (destino / "Cookies").read_text(encoding="utf-8") == "datos"
    assert not (tmp_path / "destino-tmp").exists()


def test_preparar_perfil_respeta_un_perfil_ya_copiado(tmp_path: Path, monkeypatch) -> None:
    origen = tmp_path / "origen"
    origen.mkdir()
    monkeypatch.setenv("SRI_PERFIL_ORIGEN", str(origen))
    destino = tmp_path / "destino"
    destino.mkdir()
    (destino / "Cookies").write_text("anterior", encoding="utf-8")

    _preparar_perfil(_configuracion(destino))

    assert (destino / "Cookies").read_text(encoding="utf-8") == "anterior"


def test_preparar_perfil_sin_origen_no_hace_nada(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SRI_PERFIL_ORIGEN", raising=False)
    destino = tmp_path / "destino"

    _preparar_perfil(_configuracion(destino))

    assert not destino.exists()

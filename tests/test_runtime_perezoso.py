"""El runtime pesado solo se prepara cuando hay descargas pendientes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sri_txt_robot import pipeline
from sri_txt_robot.config import PortalConfig, RobotConfig, RobotEntry
from sri_txt_robot.models import Periodo
from sri_txt_robot.pipeline import Opciones, Robot

RUC = "1790012345001"


class _Almacenamiento:
    def __init__(self, existe: bool) -> None:
        self._existe = existe

    def existe(self, _clave: str) -> bool:
        return self._existe

    def guardar_txt(self, _clave: str, _contenido: bytes) -> str:
        return ""

    def guardar_json(self, _clave: str, _datos: dict[str, Any]) -> str:
        return ""


class _NavegadorFalso:
    construidos = 0

    def __init__(self, *_args: Any, **_opciones: Any) -> None:
        _NavegadorFalso.construidos += 1

    def __enter__(self) -> object:
        raise RuntimeError("se detiene antes de abrir Chrome")

    def __exit__(self, *_args: Any) -> bool:
        return False


def _configuracion(tmp_path: Path, rucs: tuple[str, ...] = (RUC,)) -> RobotConfig:
    return RobotConfig(
        portal=PortalConfig(download_dir=str(tmp_path / "descargas")),
        robots=tuple(RobotEntry(ruc=ruc, password="clave") for ruc in rucs),
    )


def _ejecutar(configuracion: RobotConfig, almacenamiento: _Almacenamiento, preparados: list[int]) -> None:
    Robot(
        configuracion,
        object(),  # type: ignore[arg-type] - no se usa: el navegador falso aborta antes
        almacenamiento,  # type: ignore[arg-type]
        preparar_runtime=lambda: preparados.append(1),
    ).ejecutar(Opciones(periodos=(Periodo(2026, 9),), tipos=()))


@pytest.fixture(autouse=True)
def _sin_captcha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTICAPTCHA_API_KEY", raising=False)


def test_sin_trabajo_pendiente_no_prepara_el_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _NavegadorFalso.construidos = 0
    monkeypatch.setattr(pipeline, "Navegador", _NavegadorFalso)
    preparados: list[int] = []

    _ejecutar(_configuracion(tmp_path), _Almacenamiento(existe=True), preparados)

    assert preparados == []
    assert _NavegadorFalso.construidos == 0


def test_con_trabajo_pendiente_prepara_el_runtime_antes_del_navegador(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _NavegadorFalso.construidos = 0
    monkeypatch.setattr(pipeline, "Navegador", _NavegadorFalso)
    preparados: list[int] = []

    _ejecutar(_configuracion(tmp_path), _Almacenamiento(existe=False), preparados)

    assert preparados == [1]
    assert _NavegadorFalso.construidos == 1


def test_prepara_el_runtime_una_sola_vez_para_varios_rucs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _NavegadorFalso.construidos = 0
    monkeypatch.setattr(pipeline, "Navegador", _NavegadorFalso)
    preparados: list[int] = []
    configuracion = _configuracion(tmp_path, rucs=("1790012345001", "1790012345002"))

    _ejecutar(configuracion, _Almacenamiento(existe=False), preparados)

    assert preparados == [1]
    assert _NavegadorFalso.construidos == 2

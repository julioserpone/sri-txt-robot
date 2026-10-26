"""Bootstrap del runtime ZIP: descarga, verificación y extracción."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from sri_txt_robot import runtime
from sri_txt_robot.config import PortalConfig, RobotConfig


def _tar(ruta: Path, archivos: dict[str, bytes]) -> str:
    with tarfile.open(ruta, "w:gz") as tar:
        for nombre, contenido in archivos.items():
            info = tarfile.TarInfo(nombre)
            info.size = len(contenido)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(contenido))
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _restaurar_entorno() -> object:
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)


def _configurar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bundle: Path, sha: str) -> Path:
    raiz = tmp_path / "sri-runtime"
    monkeypatch.setenv("SRI_RUNTIME_DIR", str(raiz))
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_KEY", "runtime/browser.tar.gz")
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_SHA256", sha)

    def descargar(_key: str, destino: Path, _sha: str) -> None:
        shutil.copy(bundle, destino)

    monkeypatch.setattr(runtime, "_descargar", descargar)
    return raiz


def test_sin_variables_no_hace_nada(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SRI_RUNTIME_BROWSER_KEY", raising=False)
    monkeypatch.setattr(runtime, "_descargar", lambda *_a: pytest.fail("no debe descargar"))

    runtime.asegurar_runtime(RobotConfig())

    assert not (tmp_path / "sri-runtime").exists()


def test_extrae_el_bundle_y_configura_el_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "browser.tar.gz"
    sha = _tar(
        bundle,
        {
            "version.json": b'{"playwright": "1.64.0"}',
            "bin/Xvfb": b"binario",
            "bin/xkbcomp": b"binario",
            "fonts/DejaVuSans.ttf": b"fuente",
        },
    )
    raiz = _configurar(monkeypatch, tmp_path, bundle, sha)
    xkbcomp = tmp_path / "xkb"
    monkeypatch.setattr(runtime, "RUTA_XKBCOMP", xkbcomp)

    runtime.asegurar_runtime(RobotConfig())

    assert (raiz / "version.json").is_file()
    assert (raiz / "bin" / "Xvfb").is_file()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(raiz / "ms-playwright")
    assert os.environ["PATH"].split(":")[0] == str(raiz / "bin")
    assert os.environ["LD_LIBRARY_PATH"].split(":")[0] == str(raiz / "lib")
    assert os.environ["FONTCONFIG_FILE"] == str(raiz / "fonts.conf")
    assert str(raiz / "fonts") in (raiz / "fonts.conf").read_text(encoding="utf-8")
    assert (xkbcomp / "xkbcomp").resolve() == (raiz / "bin" / "xkbcomp").resolve()


def test_el_enlace_de_xkbcomp_se_rehace_en_cada_arranque(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raiz = tmp_path / "sri-runtime"
    (raiz / "bin").mkdir(parents=True)
    (raiz / "bin" / "xkbcomp").write_text("binario", encoding="utf-8")
    destino = tmp_path / "xkb"
    destino.mkdir()
    (destino / "xkbcomp").symlink_to(tmp_path / "viejo")
    monkeypatch.setattr(runtime, "RUTA_XKBCOMP", destino)

    runtime._enlazar_xkbcomp(raiz)

    assert (destino / "xkbcomp").resolve() == (raiz / "bin" / "xkbcomp").resolve()


class _S3Falso:
    """Cliente S3 mínimo para probar la verificación de checksum."""

    def __init__(self, origen: Path) -> None:
        self._origen = origen

    def download_file(self, _bucket: str, _key: str, destino: str) -> None:
        shutil.copy(self._origen, destino)


def test_un_checksum_invalido_aborta_sin_dejar_el_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "browser.tar.gz"
    _tar(bundle, {"version.json": b"{}"})
    raiz = tmp_path / "sri-runtime"
    monkeypatch.setenv("SRI_RUNTIME_DIR", str(raiz))
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_KEY", "runtime/browser.tar.gz")
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_SHA256", "0" * 64)
    monkeypatch.setattr(runtime, "_cliente_s3", lambda: _S3Falso(bundle))

    with pytest.raises(RuntimeError, match="SHA-256"):
        runtime.asegurar_runtime(RobotConfig())

    assert not raiz.exists()
    assert not raiz.with_name("sri-runtime-tmp").exists()


def test_un_bundle_ya_extraido_no_se_vuelve_a_descargar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raiz = tmp_path / "sri-runtime"
    raiz.mkdir()
    (raiz / "version.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SRI_RUNTIME_DIR", str(raiz))
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_KEY", "runtime/browser.tar.gz")
    monkeypatch.setenv("SRI_RUNTIME_BROWSER_SHA256", "x" * 64)
    monkeypatch.setattr(runtime, "_descargar", lambda *_a: pytest.fail("no debe descargar"))

    runtime.asegurar_runtime(RobotConfig())

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(raiz / "ms-playwright")


def test_extrae_el_perfil_en_la_ruta_configurada(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "browser.tar.gz"
    sha_bundle = _tar(bundle, {"version.json": b"{}"})
    _configurar(monkeypatch, tmp_path, bundle, sha_bundle)

    perfil = tmp_path / "perfil.tar.gz"
    sha_perfil = _tar(perfil, {"Cookies": b"datos", "Default/Preferences": b"{}"})
    monkeypatch.setenv("SRI_RUNTIME_PERFIL_KEY", "runtime/perfil.tar.gz")
    monkeypatch.setenv("SRI_RUNTIME_PERFIL_SHA256", sha_perfil)

    def descargar(key: str, destino: Path, _sha: str) -> None:
        shutil.copy(perfil if "perfil" in key else bundle, destino)

    monkeypatch.setattr(runtime, "_descargar", descargar)

    destino = tmp_path / "perfil"
    runtime.asegurar_runtime(RobotConfig(portal=PortalConfig(perfil_dir=str(destino))))

    assert (destino / "Cookies").read_text(encoding="utf-8") == "datos"
    assert (destino / "Default" / "Preferences").is_file()


def test_comando_xvfb_usa_la_base_de_teclados_del_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from sri_txt_robot.portal.session import _comando_xvfb

    monkeypatch.setenv("SRI_XKB_DIR", "/tmp/sri-runtime/xkb")

    assert _comando_xvfb(":99") == [
        "Xvfb",
        "-xkbdir",
        "/tmp/sri-runtime/xkb",
        ":99",
        "-screen",
        "0",
        "1440x900x24",
    ]

    monkeypatch.delenv("SRI_XKB_DIR")
    assert _comando_xvfb(":99")[1] == ":99"

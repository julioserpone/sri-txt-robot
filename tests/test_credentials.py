"""Credenciales selladas con sodium_crypto_box_seal."""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sri_txt_robot.cli import app
from sri_txt_robot.credentials import (
    CredencialesError,
    CredencialesSelladas,
    abrir_credenciales,
    generar_par_claves,
    max_edad_desde_entorno,
    sellar_credenciales,
)

RUC = "1790012345001"
CLAVE = "clave-de-prueba"


def test_ida_y_vuelta_conserva_ruc_clave_y_dato_adicional() -> None:
    publica, privada = generar_par_claves()

    sellada = sellar_credenciales(
        ruc=RUC,
        password=CLAVE,
        public_key_b64=publica,
        clave_adicional="1234567890",
    )
    credenciales = abrir_credenciales(sellada, privada)

    assert credenciales.ruc == RUC
    assert credenciales.password.get_secret_value() == CLAVE
    assert credenciales.clave_adicional == "1234567890"
    assert credenciales.iat is not None


def test_la_clave_adicional_nula_se_normaliza_a_vacio() -> None:
    credenciales = CredencialesSelladas(ruc=RUC, password=CLAVE, claveAdicional=None)

    assert credenciales.clave_adicional == ""


def test_la_representacion_no_expone_la_clave() -> None:
    publica, privada = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    credenciales = abrir_credenciales(sellada, privada)

    assert CLAVE not in repr(credenciales)


def test_una_clave_privada_distinta_no_puede_abrir_el_sobre() -> None:
    publica, _privada_correcta = generar_par_claves()
    _otra_publica, otra_privada = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    with pytest.raises(CredencialesError, match="No se pudo descifrar"):
        abrir_credenciales(sellada, otra_privada)


def test_la_clave_privada_anterior_permite_rotar_sin_cortar_invocaciones() -> None:
    publica_anterior, privada_anterior = generar_par_claves()
    _publica_nueva, privada_nueva = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica_anterior)

    credenciales = abrir_credenciales(sellada, (privada_nueva, privada_anterior))

    assert credenciales.password.get_secret_value() == CLAVE


def test_una_clave_actual_mal_formada_no_impide_usar_la_anterior() -> None:
    publica_anterior, privada_anterior = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica_anterior)

    credenciales = abrir_credenciales(sellada, ("no-es-base64", privada_anterior))

    assert credenciales.password.get_secret_value() == CLAVE


def test_acepta_una_cedula_de_diez_digitos() -> None:
    publica, privada = generar_par_claves()
    sellada = sellar_credenciales(ruc="1234567890", password=CLAVE, public_key_b64=publica)

    credenciales = abrir_credenciales(sellada, privada)

    assert credenciales.ruc == "1234567890"


def test_un_ruc_invalido_no_se_puede_sellar() -> None:
    publica, _privada = generar_par_claves()

    with pytest.raises(CredencialesError, match="no son válidos"):
        sellar_credenciales(ruc="ABC", password=CLAVE, public_key_b64=publica)


def test_un_sobre_que_no_es_base64_falla() -> None:
    _publica, privada = generar_par_claves()

    with pytest.raises(CredencialesError, match="base64"):
        abrir_credenciales("esto no es base64", privada)


def test_un_sobre_caducado_se_rechaza() -> None:
    publica, privada = generar_par_claves()
    sellada = sellar_credenciales(
        ruc=RUC,
        password=CLAVE,
        public_key_b64=publica,
        iat=int(time.time()) - 600,
    )

    with pytest.raises(CredencialesError, match="caducado"):
        abrir_credenciales(sellada, privada, max_age_seconds=60)


def test_un_sobre_con_fecha_futura_se_rechaza() -> None:
    publica, privada = generar_par_claves()
    sellada = sellar_credenciales(
        ruc=RUC,
        password=CLAVE,
        public_key_b64=publica,
        iat=int(time.time()) + 3600,
    )

    with pytest.raises(CredencialesError, match="futura"):
        abrir_credenciales(sellada, privada)


def test_max_edad_cero_desactiva_la_caducidad() -> None:
    publica, privada = generar_par_claves()
    sellada = sellar_credenciales(
        ruc=RUC,
        password=CLAVE,
        public_key_b64=publica,
        iat=1,
    )

    credenciales = abrir_credenciales(sellada, privada, max_age_seconds=None)

    assert credenciales.ruc == RUC


def test_una_clave_privada_invalida_falla_con_mensaje_claro() -> None:
    publica, _privada = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    with pytest.raises(CredencialesError, match="clave privada"):
        abrir_credenciales(sellada, base64.b64encode(b"corta").decode("ascii"))


def test_sin_clave_privada_falla_antes_de_intentar_descifrar() -> None:
    publica, _privada = generar_par_claves()
    sellada = sellar_credenciales(ruc=RUC, password=CLAVE, public_key_b64=publica)

    with pytest.raises(CredencialesError, match="Falta la clave privada"):
        abrir_credenciales(sellada, ())


def test_la_antiguedad_maxima_se_lee_del_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "0")
    assert max_edad_desde_entorno() is None

    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "120")
    assert max_edad_desde_entorno() == 120

    monkeypatch.setenv("SRI_CREDENTIALS_MAX_AGE_SECONDS", "no-es-numero")
    with pytest.raises(CredencialesError, match="entero"):
        max_edad_desde_entorno()


def test_los_comandos_de_cli_generan_y_sellan(tmp_path: Path) -> None:
    publica = tmp_path / "publica.b64"
    privada = tmp_path / "privada.b64"
    runner = CliRunner()

    generado = runner.invoke(
        app,
        ["generar-claves", "--publica", str(publica), "--privada", str(privada)],
    )
    assert generado.exit_code == 0
    assert publica.is_file()
    assert privada.is_file()

    sellado = runner.invoke(
        app,
        [
            "sellar-credenciales",
            "--ruc",
            RUC,
            "--public-key",
            str(publica),
            "--password",
            CLAVE,
        ],
    )
    assert sellado.exit_code == 0

    credenciales = abrir_credenciales(
        sellado.stdout.strip(),
        privada.read_text(encoding="utf-8").strip(),
    )
    assert credenciales.ruc == RUC
    assert credenciales.password.get_secret_value() == CLAVE

"""Pruebas de la extracción de claves de acceso."""

from __future__ import annotations

import pytest

from sri_txt_robot.manifest import (
    extraer_claves,
    nombre_claves,
    nombre_manifiesto,
    nombre_txt,
    verificador_valido,
)
from sri_txt_robot.models import TipoComprobante

#: Clave válida generada con AccessKeyGenerator del paquete sri-comprobantes-php.
CLAVE_VALIDA = "1509202601179001234500120010010000001231234567812"

#: Misma clave con el verificador alterado: no debe aceptarse.
CLAVE_INVALIDA = "1509202601179001234500120010010000001231234567813"


def test_el_verificador_acepta_una_clave_generada_por_el_paquete_php() -> None:
    assert verificador_valido(CLAVE_VALIDA) is True


def test_el_verificador_rechaza_un_digito_modificado() -> None:
    assert verificador_valido(CLAVE_INVALIDA) is False


def test_el_verificador_rechaza_longitudes_distintas_de_49() -> None:
    assert verificador_valido("123") is False
    assert verificador_valido("1" * 50) is False


def test_el_verificador_rechaza_caracteres_no_numericos() -> None:
    assert verificador_valido("a" * 49) is False


def test_extrae_la_clave_de_un_txt_con_ruido() -> None:
    contenido = (
        "Reporte de comprobantes electrónicos recibidos\n"
        "Tipo de comprobante: Factura\n"
        f"001 | 1760002600001 | BANCO CENTRAL DEL ECUADOR | Factura 001-001-000000001 | CA:{CLAVE_VALIDA}\n"
        f"NA:{CLAVE_VALIDA}\n"
    )

    claves = extraer_claves(contenido)

    assert claves == (CLAVE_VALIDA,)


def test_no_repite_claves_que_aparecen_varias_veces() -> None:
    contenido = f"{CLAVE_VALIDA}\n{CLAVE_VALIDA}\n"

    assert len(extraer_claves(contenido)) == 1


def test_descarta_numeros_de_49_digitos_con_verificador_incorrecto() -> None:
    contenido = f"{CLAVE_INVALIDA}\n{CLAVE_VALIDA}\n"

    assert extraer_claves(contenido) == (CLAVE_VALIDA,)


def test_no_toma_ceros_a_la_izquierda_de_una_cifra_mas_larga() -> None:
    # 50 dígitos: la clave está embebida y no debe extraerse.
    contenido = "1" + CLAVE_VALIDA

    assert extraer_claves(contenido) == ()


def test_acepta_bytes_con_codificacion_desconocida() -> None:
    contenido = b"\xff\xfe datos " + CLAVE_VALIDA.encode("ascii")

    assert extraer_claves(contenido) == (CLAVE_VALIDA,)


def test_los_nombres_de_archivo_siguen_el_conv_pre_acordado() -> None:
    assert nombre_txt("1790012345001", "2026-09", TipoComprobante.FACTURA) == (
        "1790012345001/2026-09/comprobantes-recibidos-01.txt"
    )
    assert nombre_claves("1790012345001", "2026-09", TipoComprobante.RETENCION) == (
        "1790012345001/2026-09/comprobantes-recibidos-08.claves.json"
    )
    assert nombre_manifiesto("1790012345001", "2026-09") == "1790012345001/2026-09/manifest.json"


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        ("01", TipoComprobante.FACTURA),
        ("03", TipoComprobante.LIQUIDACION_COMPRA),
        ("05", TipoComprobante.NOTA_CREDITO),
        ("07", TipoComprobante.NOTA_DEBITO),
        ("08", TipoComprobante.RETENCION),
    ],
)
def test_los_codigos_de_tipo_se_normalizan(codigo: str, esperado: TipoComprobante) -> None:
    assert TipoComprobante.desde_codigo(codigo) is esperado

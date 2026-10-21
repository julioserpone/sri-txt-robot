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
        "dev/1790012345001/2026-09/facturas-recibidas-2026-09.txt"
    )
    assert nombre_claves("1790012345001", "2026-09", TipoComprobante.RETENCION, ambiente="produccion") == (
        "produccion/1790012345001/2026-09/retenciones-recibidas-2026-09-claves.json"
    )
    assert nombre_manifiesto("1790012345001", "2026-09", ambiente="test") == (
        "test/1790012345001/2026-09/manifest.json"
    )


@pytest.mark.parametrize(
    ("tipo", "archivo"),
    [
        (TipoComprobante.FACTURA, "facturas-recibidas-2026-09.txt"),
        (TipoComprobante.LIQUIDACION_COMPRA, "liquidaciones-compra-recibidas-2026-09.txt"),
        (TipoComprobante.NOTA_CREDITO, "notas-credito-recibidas-2026-09.txt"),
        (TipoComprobante.NOTA_DEBITO, "notas-debito-recibidas-2026-09.txt"),
        (TipoComprobante.RETENCION, "retenciones-recibidas-2026-09.txt"),
    ],
)
def test_cada_tipo_tiene_un_nombre_reconocible(tipo: TipoComprobante, archivo: str) -> None:
    assert nombre_txt("1790012345001", "2026-09", tipo, ambiente="test").endswith("/" + archivo)


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        ("01", TipoComprobante.FACTURA),
        ("03", TipoComprobante.LIQUIDACION_COMPRA),
        ("04", TipoComprobante.NOTA_CREDITO),
        ("05", TipoComprobante.NOTA_DEBITO),
        ("07", TipoComprobante.RETENCION),
    ],
)
def test_los_codigos_de_tipo_se_normalizan(codigo: str, esperado: TipoComprobante) -> None:
    assert TipoComprobante.desde_codigo(codigo) is esperado


def test_los_codigos_de_tipo_admiten_una_sola_cifra() -> None:
    assert TipoComprobante.desde_codigo("1") is TipoComprobante.FACTURA


def test_los_valores_del_portal_no_se_confunden_con_codigos_oficiales() -> None:
    # El portal numera sus opciones 1, 2, 3, 4 y 6; la ficha técnica usa
    # 01, 03, 04, 05 y 07. La conversión debe ser explícita.
    assert TipoComprobante.FACTURA.valor_portal == "1"
    assert TipoComprobante.LIQUIDACION_COMPRA.valor_portal == "2"
    assert TipoComprobante.NOTA_CREDITO.valor_portal == "3"
    assert TipoComprobante.NOTA_DEBITO.valor_portal == "4"
    assert TipoComprobante.RETENCION.valor_portal == "6"


# -- Formato real del reporte del portal ---------------------------------

CABECERA_REAL = "\t".join(
    [
        "RUC_EMISOR",
        "RAZON_SOCIAL_EMISOR",
        "TIPO_COMPROBANTE",
        "SERIE_COMPROBANTE",
        "CLAVE_ACCESO",
        "FECHA_AUTORIZACION",
        "FECHA_EMISION",
        "IDENTIFICACION_RECEPTOR",
        "VALOR_SIN_IMPUESTOS",
        "IVA",
        "IMPORTE_TOTAL",
        "NUMERO_DOCUMENTO_MODIFICADO",
    ]
)

CLAVE_FACTURA = "1509202601179001234500120010010000001231234567812"
CLAVE_NOTA_CREDITO = "1509202604179001234500120010010000004561234567812"
CLAVE_RETENCION = "1509202607179001234500120010010000007891234567812"


def _reporte_real(*filas: tuple[str, ...]) -> str:
    return "\n".join([CABECERA_REAL, *("\t".join(fila) for fila in filas)]) + "\n"


def test_extrae_la_clave_de_la_columna_del_encabezado() -> None:
    contenido = _reporte_real(
        (
            "1791927966001",
            "PLAN AUTOMOTOR ECUATORIANO PLANAUTOMOTOR S.A.S.",
            "Factura",
            "001-001-003031644",
            CLAVE_FACTURA,
            "01/09/2026 19:03:21",
            "01/09/2026",
            "1758871873",
            "56.32",
            "8.2",
            "64.52",
            "",
        ),
        (
            "1790041220001",
            "COMERCIAL KYWI S.A.",
            "Notas de Crédito",
            "001-001-000000456",
            CLAVE_NOTA_CREDITO,
            "02/09/2026 17:49:40",
            "02/09/2026",
            "1758871873",
            "4.21",
            "0.51",
            "4.72",
            "",
        ),
    )

    assert extraer_claves(contenido) == (CLAVE_FACTURA, CLAVE_NOTA_CREDITO)


def test_ignora_los_numeros_largos_de_otras_columnas() -> None:
    # La razón social y la serie no deben confundirse con la clave aunque
    # contengan cifras.
    contenido = _reporte_real(
        (
            "1791927966001",
            "1234567890123456789012345678901234567890123456789",
            "Factura",
            "001-001-003031644",
            CLAVE_RETENCION,
            "01/09/2026 19:03:21",
            "01/09/2026",
            "1758871873",
            "56.32",
            "8.2",
            "64.52",
            "",
        )
    )

    assert extraer_claves(contenido) == (CLAVE_RETENCION,)


def test_si_no_hay_encabezado_recurre_al_patron_de_49_digitos() -> None:
    contenido = f"Reporte sin encabezado\nCA:{CLAVE_FACTURA}\n"

    assert extraer_claves(contenido) == (CLAVE_FACTURA,)


def test_un_encabezado_sin_la_columna_de_clave_recurre_al_patron() -> None:
    contenido = f"RUC_EMISOR\tRAZON_SOCIAL_EMISOR\n1791927966001\tPLAN AUTOMOTOR\t{CLAVE_FACTURA}\n"

    assert extraer_claves(contenido) == (CLAVE_FACTURA,)


def test_el_parser_admite_el_txt_como_bytes() -> None:
    contenido = _reporte_real(
        (
            "1791927966001",
            "PLAN AUTOMOTOR",
            "Factura",
            "001-001-003031644",
            CLAVE_FACTURA,
            "01/09/2026 19:03:21",
            "01/09/2026",
            "1758871873",
            "56.32",
            "8.2",
            "64.52",
            "",
        )
    )

    assert extraer_claves(contenido.encode("utf-8")) == (CLAVE_FACTURA,)

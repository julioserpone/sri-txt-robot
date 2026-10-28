"""Pruebas de descarga de XML por fila de la tabla de resultados."""

from __future__ import annotations

from unittest.mock import MagicMock

from sri_txt_robot.models import Periodo, TipoComprobante
from sri_txt_robot.portal.received import ResumenXml, descargar_xmls_de_tabla


def _crear_pagina_mock(claves_por_pagina: dict[int, list[str]]) -> MagicMock:
    """Crea un mock de Page con tabla y paginador simulados."""
    pagina = MagicMock()
    pagina.pagina_actual = 1
    pagina.paginas_visitadas = []

    def locator(selector: str) -> MagicMock:
        loc = MagicMock()
        if "fila_tabla" in selector or selector == "tr":
            claves = claves_por_pagina.get(pagina.pagina_actual, [])
            loc.count.return_value = len(claves)
            loc.all.return_value = [_crear_fila_mock(clave) for clave in claves]
            return loc
        if "enlace_xml" in selector:
            loc.count.return_value = 1
            loc.first.return_value = loc
            loc.is_visible.return_value = True
            loc.is_enabled.return_value = True
            return loc
        if "celda_clave" in selector:
            loc.count.return_value = 1
            loc.all.return_value = [MagicMock(inner_text=MagicMock(return_value=""))]
            return loc
        if "paginador_siguiente" in selector:
            loc.count.return_value = 1
            loc.is_visible.return_value = True
            loc.is_enabled.return_value = True

            primero = MagicMock()
            primero.count.return_value = 1
            primero.is_visible.return_value = True
            primero.is_enabled.return_value = True
            loc.first = primero

            def avanzar(*_args, **_kwargs):
                pagina.pagina_actual += 1
                pagina.paginas_visitadas.append(pagina.pagina_actual)

            loc.click.side_effect = avanzar
            primero.click.side_effect = avanzar
            return loc
        if "paginador_desactivado" in selector:
            loc.count.return_value = 0
            loc.first.return_value = loc
            loc.is_visible.return_value = False
            return loc
        loc.count.return_value = 0
        return loc

    pagina.locator = locator

    def expect_download(_predicate=None, **_kwargs):
        from pathlib import Path

        cm = MagicMock()
        descarga = MagicMock()
        descarga.suggested_filename = "comprobante.xml"
        descarga.save_as = lambda ruta: Path(ruta).write_bytes(b"<xml-falso/>")
        cm.__enter__.return_value = cm  # info es el gestor de contexto
        cm.value = descarga             # info.value es la descarga
        cm.__exit__.return_value = False
        return cm

    pagina.expect_download.side_effect = expect_download
    pagina.wait_for_timeout = MagicMock()
    return pagina


def _crear_fila_mock(clave_acceso: str) -> MagicMock:
    fila = MagicMock()
    fila.locator = MagicMock(
        side_effect=lambda sel: (
            _crear_celda_mock(clave_acceso) if "celda_clave" in sel else _crear_enlace_mock()
        )
    )
    return fila


def _crear_celda_mock(texto: str) -> MagicMock:
    celda = MagicMock()
    celda.inner_text.return_value = texto
    celda.all.return_value = [celda]
    return celda


def _crear_enlace_mock() -> MagicMock:
    enlace = MagicMock()
    enlace.count.return_value = 1
    enlace.is_visible.return_value = True
    enlace.is_enabled.return_value = True

    primero = MagicMock()
    primero.count.return_value = 1
    primero.is_visible.return_value = True
    enlace.first = primero
    return enlace


def _almacenamiento_mock() -> MagicMock:
    almacenamiento = MagicMock()
    guardados: dict[str, bytes] = {}

    def existe(clave: str) -> bool:
        return clave in guardados

    def guardar_xml(clave: str, contenido: bytes) -> str:
        guardados[clave] = contenido
        return clave

    almacenamiento.existe.side_effect = existe
    almacenamiento.guardar_xml.side_effect = guardar_xml
    almacenamiento.guardados = guardados  # type: ignore[attr-defined]
    return almacenamiento


def _selectores() -> object:
    from sri_txt_robot.portal.selectors import Selectores

    return Selectores(
        login={},
        acuerdo={},
        menu={},
        consulta={
            "fila_tabla": ["fila_tabla"],
            "enlace_xml_fila": ["enlace_xml"],
            "celda_clave_acceso": ["celda_clave"],
            "paginador_siguiente": ["paginador_siguiente"],
            "paginador_desactivado": ["paginador_desactivado"],
        },
        captcha={},
        sesion={},
    )


def test_descarga_xml_por_fila_con_paginador() -> None:
    pagina = _crear_pagina_mock({1: ["CLAVE-001"], 2: ["CLAVE-002"]})
    almacenamiento = _almacenamiento_mock()
    periodo = Periodo(anio=2026, mes=8)
    tipo = TipoComprobante.FACTURA

    resumen = descargar_xmls_de_tabla(
        pagina,
        _selectores(),
        almacenamiento,
        "1790012345001",
        periodo,
        tipo,
        "dev",
        claves_txt=(),
    )

    assert resumen.paginas_recorridas == 2, resumen
    assert resumen.filas_vistas == 2, resumen
    assert resumen.xmls_descargados == 2, resumen
    assert resumen.xmls_omitidos_existentes == 0, resumen
    assert len(resumen.fallos) == 0, resumen
    assert (
        "dev/1790012345001/2026-08/facturas-recibidas-2026-08-xml/CLAVE-001.xml"
        in almacenamiento.guardados  # type: ignore[attr-defined]
    )
    assert (
        "dev/1790012345001/2026-08/facturas-recibidas-2026-08-xml/CLAVE-002.xml"
        in almacenamiento.guardados  # type: ignore[attr-defined]
    )


def test_omite_xml_ya_existente_idempotencia() -> None:
    pagina = _crear_pagina_mock({1: ["CLAVE-001"], 2: ["CLAVE-002"]})
    almacenamiento = _almacenamiento_mock()
    almacenamiento.guardados[  # type: ignore[attr-defined]
        "dev/1790012345001/2026-08/facturas-recibidas-2026-08-xml/CLAVE-001.xml"
    ] = b"<xml previo/>"
    periodo = Periodo(anio=2026, mes=8)
    tipo = TipoComprobante.FACTURA

    resumen = descargar_xmls_de_tabla(
        pagina,
        _selectores(),
        almacenamiento,
        "1790012345001",
        periodo,
        tipo,
        "dev",
        claves_txt=(),
    )

    assert resumen.xmls_descargados == 1, resumen
    assert resumen.xmls_omitidos_existentes == 1, resumen
    assert len(resumen.fallos) == 0, resumen


def test_sin_selectores_devuelve_resumen_vacio() -> None:
    pagina = _crear_pagina_mock({})
    almacenamiento = _almacenamiento_mock()
    periodo = Periodo(anio=2026, mes=8)
    tipo = TipoComprobante.FACTURA

    from sri_txt_robot.portal.selectors import Selectores

    vacios = Selectores(login={}, acuerdo={}, menu={}, consulta={}, captcha={}, sesion={})

    resumen = descargar_xmls_de_tabla(
        pagina,
        vacios,
        almacenamiento,
        "1790012345001",
        periodo,
        tipo,
        "dev",
        claves_txt=(),
    )

    assert resumen.paginas_recorridas == 0
    assert resumen.filas_vistas == 0
    assert resumen.xmls_descargados == 0


def test_resumen_xml_campos() -> None:
    resumen = ResumenXml()
    resumen.paginas_recorridas = 3
    resumen.filas_vistas = 10
    resumen.xmls_descargados = 8
    resumen.xmls_omitidos_existentes = 1
    resumen.rutas_xml = ("a.xml", "b.xml")
    resumen.fallos.append({"clave": "x", "pagina": "2", "error": "timeout"})

    assert resumen.paginas_recorridas == 3
    assert resumen.filas_vistas == 10
    assert resumen.xmls_descargados == 8
    assert resumen.xmls_omitidos_existentes == 1
    assert resumen.rutas_xml == ("a.xml", "b.xml")
    assert len(resumen.fallos) == 1

def test_consulta_sin_descargar_txt_deja_la_tabla_en_pantalla(monkeypatch) -> None:
    """``descargar_txt=False`` envía la consulta pero no pide el reporte TXT."""
    from sri_txt_robot.config import CaptchaConfig
    from sri_txt_robot.portal import received

    monkeypatch.setattr(received, "primer_visible", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(received, "_pulsar_consultar", lambda *_a, **_k: "boton")
    monkeypatch.setattr(received, "_esperar_resultado", lambda *_a, **_k: "ok")

    def no_descargar(*_args, **_kwargs):
        raise AssertionError("no debe pedir el reporte TXT")

    monkeypatch.setattr(received, "_descargar", no_descargar)

    resultado = received.consultar_y_descargar(
        MagicMock(url="https://portal"),
        _selectores(),
        None,
        CaptchaConfig(),
        _tmp_dir(),
        descargar_txt=False,
    )

    assert resultado.contenido == b""
    assert resultado.origen_captcha == "navegador"


def _tmp_dir():
    from pathlib import Path

    return Path("/tmp/sri-test-xml")


def test_pipeline_no_rompe_con_txt_previo_y_usa_la_tabla(monkeypatch) -> None:
    """Con TXT ya guardado se consulta sin descargar el TXT y se bajan los XML."""
    from sri_txt_robot import pipeline
    from sri_txt_robot.config import PortalConfig, RobotConfig, RobotEntry
    from sri_txt_robot.models import ConfigRuc, Credenciales

    almacenamiento = _almacenamiento_mock()
    almacenamiento.existe.side_effect = lambda _clave: True
    llamadas: list[bool] = []

    def consulta_falsa(*_args, **kwargs):
        llamadas.append(kwargs.get("descargar_txt", True))
        from sri_txt_robot.portal.received import ResultadoDescarga

        return ResultadoDescarga(contenido=b"", nombre_archivo="", origen_captcha="navegador")

    xmls: list[object] = []

    def xmls_falsos(*_args, **_kwargs):
        xmls.append(1)
        return ResumenXml(xmls_descargados=2, rutas_xml=("a.xml", "b.xml"))

    monkeypatch.setattr(pipeline, "consultar_y_descargar", consulta_falsa)
    monkeypatch.setattr(pipeline, "descargar_xmls_de_tabla", xmls_falsos)
    monkeypatch.setattr(pipeline, "navegar_a_recibidos", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "configurar_filtros", lambda *_a, **_k: None)

    robot = pipeline.Robot(
        RobotConfig(
            portal=PortalConfig(download_dir="/tmp"),
            robots=(RobotEntry(ruc="1790012345001", password="x"),),
        ),
        _selectores(),
        almacenamiento,
    )
    ruc = ConfigRuc(
        ruc="1790012345001",
        credenciales=Credenciales(ruc="1790012345001", password="x"),
    )
    resumen = pipeline.Resumen()
    robot._procesar_tipo(
        MagicMock(),
        ruc,
        Periodo(anio=2026, mes=8),
        TipoComprobante.FACTURA,
        pipeline.Opciones(periodos=(Periodo(anio=2026, mes=8),)),
        resumen,
        txt_ya_existia=True,
    )

    assert llamadas == [False], "no debe volver a pedir el TXT"
    assert xmls, "debe recorrer la tabla para bajar los XML"
    assert resumen.resultados[0].xmls_descargados == 2
    assert resumen.fallos == []

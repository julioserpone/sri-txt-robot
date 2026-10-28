"""Pruebas de descarga de XML por fila de la tabla de resultados."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from sri_txt_robot.models import Periodo, TipoComprobante
from sri_txt_robot.portal.received import ResumenXml, descargar_xmls_de_tabla

#: Clave de acceso real: 49 dígitos.
CLAVE_001 = "0107202601099130649800120890170019923691234567811"
CLAVE_002 = "0207202601176815426000120010120710515363496934312"
CLAVE_003 = "0307202601176815426000120010120710515363496934313"

#: Selectores con la misma forma que ``config/selectors.yaml``.
FILAS = '[id="frmPrincipal:tablaCompRecibidos_data"] tr'
ENLACE_XML = 'a[id*=":lnkXml"]'
CELDA_CLAVE = "td:nth-child(4)"
SIGUIENTE = ".ui-paginator-next"
DESACTIVADO = ".ui-paginator-next.ui-state-disabled"


class _Tabla:
    """Simula la tabla PrimeFaces: filas por página y paginador AJAX."""

    def __init__(self, paginas: list[list[str]], *, clave_en_fila: bool = True) -> None:
        self.paginas = paginas
        self.clave_en_fila = clave_en_fila
        self.pagina_actual = 0
        self.paginas_visitadas: list[int] = []

    @property
    def filas(self) -> list[str]:
        return self.paginas[self.pagina_actual]

    def locator(self, selector: str) -> MagicMock:
        loc = MagicMock()
        if selector == FILAS:
            loc.count.return_value = len(self.filas)
            loc.nth.side_effect = lambda i: _fila_mock(self.filas[i], self.clave_en_fila)
            return loc
        if selector == SIGUIENTE:
            ultima = self.pagina_actual == len(self.paginas) - 1
            boton = MagicMock()
            boton.count.return_value = 1
            boton.is_visible.return_value = True
            boton.is_enabled.return_value = not ultima
            loc.first = boton
            return loc
        if selector == DESACTIVADO:
            ultima = self.pagina_actual == len(self.paginas) - 1
            loc.count.return_value = 1 if ultima else 0
            loc.first = MagicMock(is_visible=MagicMock(return_value=ultima))
            return loc
        loc.count.return_value = 0
        return loc

    def siguiente(self, *_args, **_kwargs) -> None:
        self.pagina_actual += 1
        self.paginas_visitadas.append(self.pagina_actual)


def _fila_mock(clave: str, clave_en_fila: bool) -> MagicMock:
    fila = MagicMock()
    celda = MagicMock()
    celda.inner_text.return_value = clave if clave_en_fila else ""

    def locator(selector: str) -> MagicMock:
        loc = MagicMock()
        if selector == CELDA_CLAVE:
            loc.all.return_value = [celda]
        elif selector == ENLACE_XML:
            enlace = MagicMock(count=MagicMock(return_value=1), is_visible=MagicMock(return_value=True))
            loc.first = enlace
        else:
            loc.all.return_value = []
        return loc

    fila.locator.side_effect = locator
    return fila


def _pagina_mock(tabla: _Tabla) -> MagicMock:
    pagina = MagicMock()
    pagina.locator.side_effect = tabla.locator

    def expect_download(_predicate=None, **_kwargs):
        # El portal sugiere siempre el mismo nombre ("Factura.xml").
        cm = MagicMock()
        descarga = MagicMock(suggested_filename="Factura.xml")
        descarga.save_as = lambda ruta: Path(ruta).write_bytes(b"<autorizacion/>")
        cm.__enter__.return_value = cm
        cm.value = descarga
        cm.__exit__.return_value = False
        return cm

    def expect_response(_predicate=None, **_kwargs):
        cm = MagicMock()
        cm.__enter__.side_effect = lambda: tabla.siguiente()
        cm.__exit__.return_value = False
        return cm

    pagina.expect_download.side_effect = expect_download
    pagina.expect_response.side_effect = expect_response
    pagina.wait_for_load_state = MagicMock()
    return pagina


def _almacenamiento_mock() -> MagicMock:
    almacenamiento = MagicMock()
    guardados: dict[str, bytes] = {}

    almacenamiento.existe.side_effect = lambda clave: clave in guardados
    almacenamiento.guardar_xml.side_effect = lambda clave, contenido: guardados.setdefault(clave, contenido) or clave
    almacenamiento.guardados = guardados  # type: ignore[attr-defined]
    return almacenamiento


def _selectores() -> object:
    from sri_txt_robot.portal.selectors import Selectores

    return Selectores(
        login={},
        acuerdo={},
        menu={},
        consulta={
            "fila_tabla": [FILAS],
            "enlace_xml_fila": [ENLACE_XML],
            "celda_clave_acceso": [CELDA_CLAVE],
            "paginador_siguiente": [SIGUIENTE],
            "paginador_desactivado": [DESACTIVADO],
        },
        captcha={},
        sesion={},
    )


def _descargar(tabla: _Tabla, almacenamiento: MagicMock, tmp_path: Path, **opciones) -> ResumenXml:
    return descargar_xmls_de_tabla(
        _pagina_mock(tabla),
        _selectores(),
        almacenamiento,
        "1790012345001",
        Periodo(anio=2026, mes=7),
        TipoComprobante.FACTURA,
        "produccion",
        directorio_descargas=tmp_path,
        **opciones,
    )


def _clave_logica(clave: str) -> str:
    return f"produccion/1790012345001/2026-07/facturas-recibidas-2026-07-xml/{clave}.xml"


def test_descarga_xml_de_todas_las_paginas(tmp_path: Path) -> None:
    tabla = _Tabla([[CLAVE_001, CLAVE_002], [CLAVE_003]])
    almacenamiento = _almacenamiento_mock()

    resumen = _descargar(tabla, almacenamiento, tmp_path)

    assert resumen.paginas_recorridas == 2, resumen
    assert resumen.filas_vistas == 3, resumen
    assert resumen.xmls_descargados == 3, resumen
    assert resumen.xmls_omitidos_existentes == 0, resumen
    assert resumen.fallos == [], resumen
    assert tabla.paginas_visitadas == [1]
    for clave in (CLAVE_001, CLAVE_002, CLAVE_003):
        assert _clave_logica(clave) in almacenamiento.guardados  # type: ignore[attr-defined]


def test_omite_xml_ya_existente_idempotencia(tmp_path: Path) -> None:
    almacenamiento = _almacenamiento_mock()
    almacenamiento.guardados[_clave_logica(CLAVE_001)] = b"<xml previo/>"  # type: ignore[attr-defined]
    tabla = _Tabla([[CLAVE_001, CLAVE_002]])

    resumen = _descargar(tabla, almacenamiento, tmp_path)

    assert resumen.xmls_descargados == 1, resumen
    assert resumen.xmls_omitidos_existentes == 1, resumen
    assert resumen.fallos == [], resumen


def test_sin_selectores_devuelve_resumen_vacio(tmp_path: Path) -> None:
    from sri_txt_robot.portal.selectors import Selectores

    resumen = descargar_xmls_de_tabla(
        MagicMock(),
        Selectores(login={}, acuerdo={}, menu={}, consulta={}, captcha={}, sesion={}),
        _almacenamiento_mock(),
        "1790012345001",
        Periodo(anio=2026, mes=7),
        TipoComprobante.FACTURA,
        "produccion",
        directorio_descargas=tmp_path,
    )

    assert resumen == ResumenXml()


def test_usa_la_clave_del_txt_cuando_la_fila_no_la_expone(tmp_path: Path) -> None:
    tabla = _Tabla([[CLAVE_001, CLAVE_002]], clave_en_fila=False)

    resumen = _descargar(tabla, _almacenamiento_mock(), tmp_path, claves_txt=(CLAVE_001, CLAVE_002))

    assert resumen.xmls_descargados == 2, resumen
    assert resumen.fallos == [], resumen


def test_una_celda_sin_formato_de_clave_no_se_usa_como_nombre(tmp_path: Path) -> None:
    """Un texto cualquiera en la celda no debe nombrar el XML de otro comprobante."""
    tabla = _Tabla([["FACTURA 089-017-001992369"]])

    resumen = _descargar(tabla, _almacenamiento_mock(), tmp_path)

    assert resumen.xmls_descargados == 0, resumen
    assert resumen.fallos[0]["error"] == "no se leyó la clave de acceso", resumen


def test_consulta_sin_descargar_txt_deja_la_tabla_en_pantalla(monkeypatch) -> None:
    """``descargar_txt=False`` envía la consulta pero no pide el reporte TXT."""
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
        Path("/tmp/sri-test-xml"),
        descargar_txt=False,
    )

    assert resultado.contenido == b""
    assert resultado.origen_captcha == "navegador"


def test_pipeline_baja_txt_y_xml_en_la_misma_corrida(monkeypatch, tmp_path: Path) -> None:
    """Si el TXT no existe: se consulta, se bajan los XML de la tabla y luego el TXT."""
    from sri_txt_robot import pipeline
    from sri_txt_robot.config import PortalConfig, RobotConfig, RobotEntry
    from sri_txt_robot.models import ConfigRuc, Credenciales
    from sri_txt_robot.portal.received import ResultadoDescarga

    almacenamiento = _almacenamiento_mock()
    almacenamiento.existe.side_effect = lambda _clave: False
    llamadas: list[bool] = []

    def consulta_falsa(*_args, **kwargs):
        llamadas.append(kwargs["descargar_txt"])
        return ResultadoDescarga(contenido=f"CLAVE {CLAVE_001}".encode(), nombre_archivo="f.txt")

    def xmls_falsos(*_args, **_kwargs):
        return ResumenXml(xmls_descargados=33, rutas_xml=(_clave_logica(CLAVE_001),))

    monkeypatch.setattr(pipeline, "consultar_y_descargar", consulta_falsa)
    monkeypatch.setattr(pipeline, "descargar_xmls_de_tabla", xmls_falsos)
    monkeypatch.setattr(pipeline, "navegar_a_recibidos", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "configurar_filtros", lambda *_a, **_k: None)

    robot = pipeline.Robot(
        RobotConfig(
            portal=PortalConfig(download_dir=str(tmp_path)),
            robots=(RobotEntry(ruc="1790012345001", password="x"),),
        ),
        _selectores(),
        almacenamiento,
    )
    ruc = ConfigRuc(ruc="1790012345001", credenciales=Credenciales(ruc="1790012345001", password="x"))
    resumen = pipeline.Resumen()

    robot._procesar_tipo(
        MagicMock(),
        ruc,
        Periodo(anio=2026, mes=7),
        TipoComprobante.FACTURA,
        pipeline.Opciones(periodos=(Periodo(anio=2026, mes=7),)),
        resumen,
    )

    assert llamadas == [True], "con el TXT ausente debe descargarlo"
    assert almacenamiento.guardar_txt.called, "debe guardar el TXT"
    assert resumen.resultados[0].xmls_descargados == 33
    assert resumen.fallos == []


def test_pipeline_con_txt_previo_solo_baja_xml(monkeypatch, tmp_path: Path) -> None:
    """Si el TXT ya existe: consulta sin TXT, baja los XML y no reescribe nada."""
    from sri_txt_robot import pipeline
    from sri_txt_robot.config import PortalConfig, RobotConfig, RobotEntry
    from sri_txt_robot.models import ConfigRuc, Credenciales
    from sri_txt_robot.portal.received import ResultadoDescarga

    almacenamiento = _almacenamiento_mock()
    almacenamiento.existe.side_effect = lambda _clave: True
    llamadas: list[bool] = []

    def consulta_falsa(*_args, **kwargs):
        llamadas.append(kwargs["descargar_txt"])
        return ResultadoDescarga(contenido=b"", nombre_archivo="", origen_captcha="navegador")

    def xmls_falsos(*_args, **_kwargs):
        return ResumenXml(xmls_descargados=2, rutas_xml=("a.xml", "b.xml"))

    monkeypatch.setattr(pipeline, "consultar_y_descargar", consulta_falsa)
    monkeypatch.setattr(pipeline, "descargar_xmls_de_tabla", xmls_falsos)
    monkeypatch.setattr(pipeline, "navegar_a_recibidos", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "configurar_filtros", lambda *_a, **_k: None)

    robot = pipeline.Robot(
        RobotConfig(
            portal=PortalConfig(download_dir=str(tmp_path)),
            robots=(RobotEntry(ruc="1790012345001", password="x"),),
        ),
        _selectores(),
        almacenamiento,
    )
    ruc = ConfigRuc(ruc="1790012345001", credenciales=Credenciales(ruc="1790012345001", password="x"))
    resumen = pipeline.Resumen()

    robot._procesar_tipo(
        MagicMock(),
        ruc,
        Periodo(anio=2026, mes=7),
        TipoComprobante.FACTURA,
        pipeline.Opciones(periodos=(Periodo(anio=2026, mes=7),)),
        resumen,
        txt_ya_existia=True,
    )

    assert llamadas == [False], "no debe volver a pedir el TXT"
    assert not almacenamiento.guardar_txt.called, "no debe reescribir el TXT"
    assert not almacenamiento.guardar_json.called, "no debe reescribir el JSON de claves"
    assert resumen.resultados[0].xmls_descargados == 2
    assert resumen.fallos == []


def test_los_selectores_del_yaml_detectan_la_fila_real() -> None:
    """Los selectores de ``config/selectors.yaml`` deben casar con la tabla real."""
    import yaml

    consulta = yaml.safe_load(Path("config/selectors.yaml").read_text(encoding="utf-8"))["consulta"]
    fila = (
        '<tr data-ri="0" class="ui-widget-content ui-datatable-even" role="row">'
        '<td role="gridcell"><div class="ui-dt-c">1</div></td>'
        '<td role="gridcell"><div class="ui-dt-c">0991306498001<br>NUCOPSA S.A.</div></td>'
        '<td role="gridcell"><div class="ui-dt-c">Factura  089-017-001992369</div></td>'
        '<td role="gridcell"><div class="ui-dt-c"><a id="frmPrincipal:tablaCompRecibidos:0:j_idt60" '
        f'href="#">{CLAVE_001}</a></div></td>'
        '<td role="gridcell"><div class="ui-dt-c"><a id="frmPrincipal:tablaCompRecibidos:0:lnkXml" '
        'href="#"><img src="xml.gif"></a></div></td></tr>'
    )
    celdas = re.findall(r"<td[^>]*>(.*?)</td>", fila, flags=re.S)

    assert consulta["enlace_xml_fila"] == ['a[id*=":lnkXml"]']
    assert consulta["celda_clave_acceso"] == ["td:nth-child(4)"]
    # La 4.ª columna es la clave: el selector debe apuntar a ella y no a otra.
    assert re.search(r"\d{49}", celdas[3])
    assert not re.search(r"\d{49}", celdas[2])

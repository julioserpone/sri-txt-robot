"""Fusión del manifiesto del período entre ejecuciones."""

from __future__ import annotations

from sri_txt_robot.pipeline import _fusionar_descargas

ANTERIOR = {
    "descargas": [
        {"tipo": "01", "totalClaves": 55},
        {"tipo": "07", "totalClaves": 2},
    ]
}


def test_fusion_conserva_los_tipos_que_no_se_descargaron() -> None:
    """Una ejecución idempotente no puede vaciar el manifiesto."""
    assert [d["tipo"] for d in _fusionar_descargas(ANTERIOR, [])] == ["01", "07"]


def test_fusion_reemplaza_el_tipo_de_la_ejecucion() -> None:
    fusion = _fusionar_descargas(ANTERIOR, [{"tipo": "01", "totalClaves": 50}])

    assert {d["tipo"]: d["totalClaves"] for d in fusion} == {"01": 50, "07": 2}


def test_fusion_sin_manifiesto_previo() -> None:
    assert _fusionar_descargas(None, [{"tipo": "04"}]) == [{"tipo": "04"}]


def test_fusion_ignora_entradas_sin_tipo() -> None:
    anterior = {"descargas": [{"totalClaves": 1}, "roto"]}

    assert _fusionar_descargas(anterior, []) == []

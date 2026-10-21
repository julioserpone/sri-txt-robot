"""Modelos de dominio del robot de descarga de TXT del SRI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Etiquetas visibles en el selector "Tipo de comprobante" del portal.
_ETIQUETAS: dict[str, str] = {
    "01": "Factura",
    "03": "Liquidación de Compra de Bienes y prestación de Servicios",
    "05": "Nota de Crédito",
    "07": "Nota de Débito",
    "08": "Comprobantes de Retención",
}


class TipoComprobante(StrEnum):
    """Códigos de tipo de comprobante electrónico del SRI.

    Los valores corresponden a los códigos de la ficha técnica de comprobantes
    electrónicos offline v2.34 (tipos de comprobante). El portal los presenta
    con una etiqueta legible que puede sobreescribirse en la configuración.
    """

    FACTURA = "01"
    LIQUIDACION_COMPRA = "03"
    NOTA_CREDITO = "05"
    NOTA_DEBITO = "07"
    RETENCION = "08"

    @property
    def etiqueta(self) -> str:
        """Etiqueta legible por defecto en el portal."""
        return _ETIQUETAS[self.value]

    @classmethod
    def desde_codigo(cls, codigo: str) -> TipoComprobante:
        """Devuelve el tipo a partir de su código de dos dígitos."""
        try:
            return cls(codigo.zfill(2))
        except ValueError as error:
            raise ValueError(f"Tipo de comprobante desconocido: {codigo!r}") from error


@dataclass(frozen=True, slots=True)
class Periodo:
    """Período de emisión consultado en el portal.

    ``dia`` en ``None`` significa "Todos", que es lo que permite abarcar el mes
    completo en una sola consulta.
    """

    anio: int
    mes: int
    dia: int | None = None

    def __post_init__(self) -> None:
        if not 2000 <= self.anio <= 2100:
            raise ValueError(f"Año fuera de rango: {self.anio}")
        if not 1 <= self.mes <= 12:
            raise ValueError(f"Mes fuera de rango: {self.mes}")
        if self.dia is not None and not 1 <= self.dia <= 31:
            raise ValueError(f"Día fuera de rango: {self.dia}")

    @property
    def clave(self) -> str:
        """Clave compacta del período, usada en nombres de archivo."""
        base = f"{self.anio:04d}-{self.mes:02d}"
        return base if self.dia is None else f"{base}-{self.dia:02d}"

    @property
    def es_mes_completo(self) -> bool:
        return self.dia is None

    @classmethod
    def desde_texto(cls, texto: str) -> Periodo:
        """Parsea ``YYYY-MM`` o ``YYYY-MM-DD``."""
        partes = texto.strip().split("-")
        if len(partes) not in (2, 3):
            raise ValueError(f"Período inválido: {texto!r}. Use YYYY-MM o YYYY-MM-DD.")
        anio = int(partes[0])
        mes = int(partes[1])
        dia = int(partes[2]) if len(partes) == 3 else None
        return cls(anio=anio, mes=mes, dia=dia)


@dataclass(frozen=True, slots=True)
class Credenciales:
    """Credenciales de acceso al portal del SRI.

    ``clave_adicional`` corresponde al campo ``ciAdicional`` del formulario y
    solo es necesario para RUC de 13 dígitos.
    """

    ruc: str
    password: str
    clave_adicional: str = ""

    def __post_init__(self) -> None:
        if not self.ruc.isdigit():
            raise ValueError(f"El RUC debe ser numérico: {self.ruc!r}")
        if len(self.ruc) not in (10, 13):
            raise ValueError(f"El RUC debe tener 10 o 13 dígitos: {self.ruc!r}")
        if len(self.ruc) == 13 and not self.clave_adicional:
            raise ValueError("Un RUC de 13 dígitos requiere la clave adicional (ciAdicional).")

    @property
    def requiere_clave_adicional(self) -> bool:
        return len(self.ruc) == 13


@dataclass(frozen=True, slots=True)
class ConfigRuc:
    """Configuración de un contribuyente procesado por el robot."""

    ruc: str
    credenciales: Credenciales
    tipos: tuple[TipoComprobante, ...] = field(default_factory=tuple)
    activo: bool = True
    etiqueta: str = ""


@dataclass(frozen=True, slots=True)
class ResultadoDescarga:
    """Resultado de descargar el TXT de un tipo de comprobante en un período."""

    ruc: str
    periodo: Periodo
    tipo: TipoComprobante
    clave_txt: str
    claves_acceso: tuple[str, ...]
    bytes_txt: int
    tarea_captcha: str = ""

    @property
    def total_claves(self) -> int:
        return len(self.claves_acceso)

    def a_dict(self) -> dict[str, Any]:
        return {
            "ruc": self.ruc,
            "periodo": self.periodo.clave,
            "tipo": self.tipo.value,
            "tipoEtiqueta": self.tipo.etiqueta,
            "claveTxt": self.clave_txt,
            "bytes": self.bytes_txt,
            "totalClaves": self.total_claves,
            "tareaCaptcha": self.tarea_captcha,
        }

"""Modelos de dominio del robot de descarga de TXT del SRI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Etiquetas del desplegable "Tipo de comprobante" del portal, tomadas de la
#: pantalla real. Coinciden letra por letra con las opciones del ``select``.
_ETIQUETAS: dict[str, str] = {
    "01": "Factura",
    "03": "Liquidación de compra de bienes y prestación de servicios",
    "04": "Notas de Crédito",
    "05": "Notas de Débito",
    "07": "Comprobante de Retención",
}

#: Valor de la opción en el desplegable del portal. El portal no reutiliza los
#: códigos de la ficha técnica: numera sus cinco tipos como 1, 2, 3, 4 y 6.
#: Confirmado en la pantalla "Comprobantes electrónicos recibidos" (oct-2026).
_VALORES_PORTAL: dict[str, str] = {
    "01": "1",
    "03": "2",
    "04": "3",
    "05": "4",
    "07": "6",
}


class TipoComprobante(StrEnum):
    """Códigos de tipo de comprobante electrónico del SRI.

    Los valores corresponden a la tabla 3 de la ficha técnica de comprobantes
    electrónicos offline (``docs/guia-tecnica-sri.md``): ``01`` Factura, ``03``
    Liquidación de compra, ``04`` Nota de crédito, ``05`` Nota de débito y
    ``07`` Comprobante de retención.

    El portal numera sus opciones de otra forma; para eso está
    :attr:`valor_portal`. La guía de remisión (``06``) no aparece en el
    desplegable del portal de recibidos.
    """

    FACTURA = "01"
    LIQUIDACION_COMPRA = "03"
    NOTA_CREDITO = "04"
    NOTA_DEBITO = "05"
    RETENCION = "07"

    @property
    def etiqueta(self) -> str:
        """Etiqueta visible en el portal."""
        return _ETIQUETAS[self.value]

    @property
    def valor_portal(self) -> str:
        """Valor de la opción en el ``select`` del portal."""
        return _VALORES_PORTAL[self.value]

    @classmethod
    def desde_codigo(cls, codigo: str) -> TipoComprobante:
        """Devuelve el tipo a partir de su código de la ficha técnica.

        Acepta ``"01"`` y también ``"1"``. No acepta los valores del portal
        (``"2"``, ``"3"``, ``"4"``, ``"6"``) porque se solapan con códigos
        oficiales distintos y sería ambiguo: en la configuración se usa
        siempre el código oficial.
        """
        normalizado = codigo.strip().zfill(2)
        try:
            return cls(normalizado)
        except ValueError as error:
            validos = ", ".join(miembro.value for miembro in cls)
            raise ValueError(f"Tipo de comprobante desconocido: {codigo!r}. Use uno de: {validos}.") from error


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

    ``ruc`` es el identificador que se escribe en "RUC / C.I. / Pasaporte":
    el RUC de 13 dígitos tal como lo muestra el portal. Si se configura la
    cédula de 10 dígitos, se completa con ``001`` al iniciar sesión, que es la
    forma en que el SRI compone el RUC de una persona natural.

    ``clave_adicional`` corresponde al campo opcional "C.I. adicional" y solo
    se envía si se configura.
    """

    ruc: str
    password: str
    clave_adicional: str = ""

    def __post_init__(self) -> None:
        if not self.ruc.isdigit():
            raise ValueError(f"El RUC debe ser numérico: {self.ruc!r}")
        if len(self.ruc) not in (10, 13):
            raise ValueError(f"El RUC debe tener 10 o 13 dígitos: {self.ruc!r}")

    @property
    def usuario_portal(self) -> str:
        """Identificador que se escribe en el campo del formulario de acceso."""
        return self.ruc if len(self.ruc) == 13 else f"{self.ruc}001"

    @property
    def requiere_clave_adicional(self) -> bool:
        return bool(self.clave_adicional)


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

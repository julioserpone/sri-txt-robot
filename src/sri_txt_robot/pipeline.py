"""Orquestación del robot: RUC × período × tipo de comprobante.

El robot es **idempotente**: si el TXT de una combinación ya existe en el
almacenamiento, no se vuelve a descargar salvo que se fuerce. Esto permite
reintentar la invocación tras un fallo parcial sin duplicar gastos de
anti-captcha ni volver a golpear el portal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .captcha.anticaptcha import AntiCaptchaClient
from .config import RobotConfig
from .logging import obtener_logger
from .manifest import extraer_claves, nombre_claves, nombre_manifiesto, nombre_txt
from .models import ConfigRuc, Periodo, ResultadoDescarga, TipoComprobante
from .portal.login import iniciar_sesion
from .portal.received import (
    ConsultaSRIError,
    configurar_filtros,
    consultar_y_descargar,
    navegar_a_recibidos,
)
from .portal.received import (
    ResultadoDescarga as DescargaPortal,
)
from .portal.selectors import Selectores
from .portal.session import Navegador
from .storage import Almacenamiento

logger = obtener_logger(__name__)


def _ahora() -> str:
    """Marca de tiempo ISO-8601 en UTC."""
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True, slots=True)
class Opciones:
    """Parámetros de una ejecución del robot."""

    periodo: Periodo
    tipos: tuple[TipoComprobante, ...] = ()
    ruc: str = ""
    forzar: bool = False
    max_intentos_captcha: int = 3


@dataclass(slots=True)
class Resumen:
    """Resultado agregado de una ejecución."""

    resultados: list[ResultadoDescarga] = field(default_factory=list)
    omitidos: list[str] = field(default_factory=list)
    fallos: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_claves(self) -> int:
        return sum(resultado.total_claves for resultado in self.resultados)

    def a_dict(self) -> dict[str, Any]:
        return {
            "descargados": [resultado.a_dict() for resultado in self.resultados],
            "omitidos": self.omitidos,
            "fallos": self.fallos,
            "totalClaves": self.total_claves,
        }


class Robot:
    """Descarga los TXT de comprobantes recibidos del portal del SRI."""

    def __init__(
        self,
        configuracion: RobotConfig,
        selectores: Selectores,
        almacenamiento: Almacenamiento,
    ) -> None:
        self._configuracion = configuracion
        self._selectores = selectores
        self._almacenamiento = almacenamiento
        self._cliente_captcha = AntiCaptchaClient(
            configuracion.captcha.resolver_api_key(),
            api_url=configuracion.captcha.api_url,
        )

    def ejecutar(self, opciones: Opciones) -> Resumen:
        """Ejecuta el robot para todos los RUC configurados."""
        resumen = Resumen()

        rucs = [ruc for ruc in self._configuracion.rucs_activos() if not opciones.ruc or ruc.ruc == opciones.ruc]

        if not rucs:
            logger.warning("no hay RUC configurados para procesar", extra={"ruc": opciones.ruc})
            return resumen

        logger.info(
            "iniciando ejecución del robot",
            extra={
                "rucs": [ruc.ruc for ruc in rucs],
                "periodo": opciones.periodo.clave,
                "tipos": [tipo.value for tipo in opciones.tipos],
            },
        )

        for ruc in rucs:
            self._procesar_ruc(ruc, opciones, resumen)

        return resumen

    # -- Interno --------------------------------------------------------

    def _procesar_ruc(self, ruc: ConfigRuc, opciones: Opciones, resumen: Resumen) -> None:
        tipos = opciones.tipos or ruc.tipos or tuple(TipoComprobante)
        if not tipos:
            logger.warning("no hay tipos de comprobante configurados", extra={"ruc": ruc.ruc})
            return

        pendientes = [
            tipo
            for tipo in tipos
            if opciones.forzar
            or not self._almacenamiento.existe(nombre_txt(ruc.ruc, opciones.periodo.clave, tipo))
        ]

        omitidos = [tipo.value for tipo in tipos if tipo not in pendientes]
        if omitidos:
            logger.info("descargas omitidas por idempotencia", extra={"ruc": ruc.ruc, "tipos": omitidos})
            resumen.omitidos.extend(f"{ruc.ruc}/{opciones.periodo.clave}/{codigo}" for codigo in omitidos)

        if not pendientes:
            return

        estado_sesion = self._ruta_estado(ruc.ruc)

        try:
            with Navegador(self._configuracion.portal, estado_sesion) as navegador:
                pagina = navegador.pagina
                if pagina is None:
                    raise RuntimeError("No se pudo crear la página del navegador.")

                iniciar_sesion(pagina, ruc.credenciales, self._selectores)

                for tipo in pendientes:
                    self._procesar_tipo(pagina, ruc, tipo, opciones, resumen)
        except Exception as error:  # noqa: BLE001 - se registra y se continúa con el siguiente RUC
            logger.exception("fallo al procesar el RUC", extra={"ruc": ruc.ruc})
            resumen.fallos.append({"ruc": ruc.ruc, "error": str(error)})
            return

        self._guardar_manifiesto(ruc.ruc, opciones.periodo, resumen)

    def _procesar_tipo(
        self,
        pagina: Any,
        ruc: ConfigRuc,
        tipo: TipoComprobante,
        opciones: Opciones,
        resumen: Resumen,
    ) -> None:
        clave_txt = nombre_txt(ruc.ruc, opciones.periodo.clave, tipo)
        directorio = self._directorio_descargas()

        try:
            navegar_a_recibidos(pagina, self._selectores)
            configurar_filtros(pagina, self._selectores, opciones.periodo, tipo)

            descarga: DescargaPortal = consultar_y_descargar(
                pagina,
                self._selectores,
                self._cliente_captcha,
                self._configuracion.captcha,
                directorio,
                max_intentos=opciones.max_intentos_captcha,
            )
        except ConsultaSRIError as error:
            logger.exception(
                "fallo al descargar el TXT",
                extra={"ruc": ruc.ruc, "periodo": opciones.periodo.clave, "tipo": tipo.value},
            )
            resumen.fallos.append(
                {
                    "ruc": ruc.ruc,
                    "periodo": opciones.periodo.clave,
                    "tipo": tipo.value,
                    "error": str(error),
                }
            )
            return
        except Exception as error:  # noqa: BLE001
            logger.exception(
                "error inesperado al descargar el TXT",
                extra={"ruc": ruc.ruc, "tipo": tipo.value},
            )
            resumen.fallos.append(
                {"ruc": ruc.ruc, "periodo": opciones.periodo.clave, "tipo": tipo.value, "error": str(error)}
            )
            return

        claves = extraer_claves(descarga.contenido)

        self._almacenamiento.guardar_txt(clave_txt, descarga.contenido)
        self._almacenamiento.guardar_json(
            nombre_claves(ruc.ruc, opciones.periodo.clave, tipo),
            {
                "ruc": ruc.ruc,
                "periodo": opciones.periodo.clave,
                "tipo": tipo.value,
                "tipoEtiqueta": tipo.etiqueta,
                "total": len(claves),
                "clavesAcceso": list(claves),
            },
        )

        resultado = ResultadoDescarga(
            ruc=ruc.ruc,
            periodo=opciones.periodo,
            tipo=tipo,
            clave_txt=clave_txt,
            claves_acceso=claves,
            bytes_txt=len(descarga.contenido),
            tarea_captcha=descarga.solucion.task_type if descarga.solucion else "",
        )
        resumen.resultados.append(resultado)

        logger.info(
            "TXT descargado",
            extra={
                "ruc": ruc.ruc,
                "periodo": opciones.periodo.clave,
                "tipo": tipo.value,
                "claves": len(claves),
                "bytes": len(descarga.contenido),
            },
        )

    def _guardar_manifiesto(self, ruc: str, periodo: Periodo, resumen: Resumen) -> None:
        """Guarda el manifiesto del período con el detalle de las descargas."""
        descargas = [resultado.a_dict() for resultado in resumen.resultados if resultado.ruc == ruc]
        self._almacenamiento.guardar_json(
            nombre_manifiesto(ruc, periodo.clave),
            {
                "ruc": ruc,
                "periodo": periodo.clave,
                "generadoEn": _ahora(),
                "descargas": descargas,
                "totalClaves": sum(descarga["totalClaves"] for descarga in descargas),
            },
        )
    def _ruta_estado(self, ruc: str) -> Path:
        """Ruta del archivo que preserva la cookies de la sesión."""
        base = self._configuracion.portal.download_dir or "/tmp/sri-sesion"
        return Path(base) / f"sesion-{ruc}.json"

    def _directorio_descargas(self) -> Path:
        """Directorio temporal donde Playwright materializa las descargas."""
        base = self._configuracion.portal.download_dir or "/tmp/sri-downloads"
        return Path(base)

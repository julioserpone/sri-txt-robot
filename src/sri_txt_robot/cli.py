"""Interfaz de línea de comandos del robot."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from .captcha.anticaptcha import AntiCaptchaClient
from .config import ConfigError, RobotConfig, cargar_config
from .logging import configurar_logging, obtener_logger
from .manifest import extraer_claves
from .models import Periodo, TipoComprobante
from .period import sugerir_periodo, ventana_de_descarga
from .pipeline import Opciones, Robot
from .portal.login import iniciar_sesion
from .portal.received import ConsultaSRIError, navegar_a_recibidos
from .portal.selectors import Selectores
from .portal.session import Navegador
from .storage import crear_almacenamiento

logger = obtener_logger(__name__)

app = typer.Typer(
    name="sri-txt-robot",
    help="Descarga del portal del SRI los TXT de comprobantes electrónicos recibidos.",
    no_args_is_help=True,
    add_completion=False,
)

CONFIG_POR_DEFECTO = "config/robots.yaml"
SELECTORES_POR_DEFECTO = "config/selectors.yaml"


@app.command("descargar")
def descargar(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Archivo YAML de configuración."),
    ] = Path(CONFIG_POR_DEFECTO),
    selectores: Annotated[
        Path,
        typer.Option("--selectors", "-s", help="Archivo YAML de selectores."),
    ] = Path(SELECTORES_POR_DEFECTO),
    periodo: Annotated[
        str | None,
        typer.Option("--periodo", "-p", help="Período a descargar en formato YYYY-MM."),
    ] = None,
    ruc: Annotated[
        str | None,
        typer.Option("--ruc", "-r", help="Procesa solo un RUC."),
    ] = None,
    tipos: Annotated[
        list[str] | None,
        typer.Option("--tipo", "-t", help="Tipo de comprobante a descargar. Repetible."),
    ] = None,
    forzar: Annotated[
        bool,
        typer.Option("--forzar", help="Vuelve a descargar aunque el TXT ya exista."),
    ] = False,
    log_nivel: Annotated[str, typer.Option("--log-nivel", help="Nivel de log.")] = "INFO",
) -> None:
    """Descarga los TXT de comprobantes recibidos."""
    configurar_logging(log_nivel)

    configuracion = _cargar(config)
    catalogo = _cargar_selectores(selectores)

    periodo_objetivo = _resolver_periodo(periodo)
    tipos_objetivo = tuple(TipoComprobante.desde_codigo(codigo) for codigo in (tipos or []))

    ventana = ventana_de_descarga(periodo_objetivo.anio, periodo_objetivo.mes)
    hoy = date.today()
    if not ventana.contiene(hoy):
        typer.secho(
            f"AVISO: el período {periodo_objetivo.clave} está fuera de la ventana de descarga "
            f"del TXT (cerró el {ventana.fin.isoformat()}).",
            fg=typer.colors.YELLOW,
            err=True,
        )

    almacenamiento = crear_almacenamiento(configuracion)
    robot = Robot(configuracion, catalogo, almacenamiento)

    resumen = robot.ejecutar(
        Opciones(
            periodo=periodo_objetivo,
            tipos=tipos_objetivo,
            ruc=ruc or "",
            forzar=forzar,
        )
    )

    typer.echo(json.dumps(resumen.a_dict(), ensure_ascii=False, indent=2))

    if resumen.fallos:
        raise typer.Exit(code=1)


@app.command("saldo")
def saldo(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Archivo YAML de configuración."),
    ] = Path(CONFIG_POR_DEFECTO),
) -> None:
    """Muestra el saldo disponible en anti-captcha."""
    configurar_logging()
    configuracion = _cargar(config)
    cliente = AntiCaptchaClient(configuracion.captcha.resolver_api_key(), api_url=configuracion.captcha.api_url)
    typer.echo(f"Saldo anti-captcha: USD {cliente.saldo():.4f}")


@app.command("periodo")
def periodo(
    hoy: Annotated[
        str | None,
        typer.Option("--hoy", help="Fecha de referencia en formato YYYY-MM-DD."),
    ] = None,
) -> None:
    """Muestra la ventana de descarga del TXT y el período sugerido."""
    fecha = date.fromisoformat(hoy) if hoy else date.today()
    anio, mes = sugerir_periodo(fecha)
    ventana = ventana_de_descarga(anio, mes)

    typer.echo(f"Fecha de referencia: {fecha.isoformat()}")
    typer.echo(f"Período sugerido:     {anio:04d}-{mes:02d}")
    typer.echo(f"Ventana de descarga:  {ventana.inicio.isoformat()} → {ventana.fin.isoformat()}")
    typer.echo(f"Días restantes:       {ventana.dias_restantes(fecha)}")


@app.command("calibrar")
def calibrar(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Archivo YAML de configuración."),
    ] = Path(CONFIG_POR_DEFECTO),
    selectores: Annotated[
        Path,
        typer.Option("--selectors", "-s", help="Archivo YAML de selectores."),
    ] = Path(SELECTORES_POR_DEFECTO),
    ruc: Annotated[
        str | None,
        typer.Option("--ruc", "-r", help="RUC a usar para la calibración."),
    ] = None,
    salida: Annotated[
        Path,
        typer.Option("--salida", help="Directorio donde escribir el diagnóstico."),
    ] = Path("diagnostico"),
) -> None:
    """Inicia sesión y vuelca el DOM de la pantalla de consulta.

    Sirve para adaptar ``config/selectors.yaml`` cuando el portal cambia: el
    comando no necesita CAPTCHA ni descarga nada, solo abre la pantalla y
    describe los desplegables, botones y enlaces que encuentra.
    """
    configurar_logging()
    configuracion = _cargar(config)
    catalogo = _cargar_selectores(selectores)

    rucs = configuracion.rucs_activos()
    if ruc:
        rucs = [item for item in rucs if item.ruc == ruc]
    if not rucs:
        typer.secho("No hay RUC configurados para calibrar.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    objetivo = rucs[0]
    salida.mkdir(parents=True, exist_ok=True)

    with Navegador(configuracion.portal, Path("/tmp") / f"sesion-{objetivo.ruc}.json") as navegador:
        pagina = navegador.pagina
        if pagina is None:
            raise typer.Exit(code=1)

        iniciar_sesion(pagina, objetivo.credenciales, catalogo)

        try:
            navegar_a_recibidos(pagina, catalogo)
        except ConsultaSRIError as error:
            typer.secho(str(error), fg=typer.colors.RED, err=True)
            (salida / "pantalla-consulta.html").write_text(pagina.content(), encoding="utf-8")
            raise typer.Exit(code=1) from error

        (salida / "pantalla-consulta.html").write_text(pagina.content(), encoding="utf-8")

        diagnostico = pagina.evaluate(
            """() => {
                const selects = Array.from(document.querySelectorAll('select')).map(s => ({
                    id: s.id || null,
                    name: s.name || null,
                    valor: s.value || null,
                    opciones: Array.from(s.options).map(o => ({ valor: o.value, texto: o.text.trim() }))
                }));
                const botones = Array.from(document.querySelectorAll('button, input[type=submit], a'))
                    .map(b => ({
                        etiqueta: (b.innerText || b.value || '').trim(),
                        id: b.id || null,
                        name: b.name || null
                    }))
                    .filter(b => b.etiqueta);
                const recaptcha = {
                    sitekey: (document.querySelector('[data-sitekey]') || {}).dataset?.sitekey || null,
                    enterprise: !!(window.grecaptcha && window.grecaptcha.enterprise),
                    campos: document.querySelectorAll('[name="g-recaptcha-response"]').length
                };
                return { selects, botones, recaptcha, url: location.href };
            }"""
        )

    (salida / "diagnostico.json").write_text(
        json.dumps(diagnostico, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    typer.echo(f"Diagnóstico escrito en {salida.resolve()}")
    typer.echo(json.dumps(diagnostico.get("recaptcha", {}), ensure_ascii=False, indent=2))
    typer.echo(f"Desplegables detectados: {len(diagnostico.get('selects', []))}")
    typer.echo(f"Botones y enlaces detectados: {len(diagnostico.get('botones', []))}")


@app.command("claves")
def claves(
    txt: Annotated[Path, typer.Argument(help="Archivo TXT descargado.")],
) -> None:
    """Extrae las claves de acceso de un TXT descargado."""
    contenido = txt.read_bytes()
    encontradas = extraer_claves(contenido)
    typer.echo(f"{len(encontradas)} claves de acceso:")
    for clave in encontradas:
        typer.echo(clave)


# -- Interno ------------------------------------------------------------


def _cargar(ruta: Path) -> RobotConfig:
    try:
        return cargar_config(ruta)
    except (ConfigError, FileNotFoundError) as error:
        typer.secho(f"Error de configuración: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error


def _cargar_selectores(ruta: Path) -> Selectores:
    try:
        return Selectores.cargar(ruta)
    except (FileNotFoundError, ValueError) as error:
        typer.secho(f"Error de selectores: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error


def _resolver_periodo(periodo: str | None) -> Periodo:
    if periodo:
        return Periodo.desde_texto(periodo)
    anio, mes = sugerir_periodo(date.today())
    return Periodo(anio=anio, mes=mes)


def main() -> None:
    """Punto de entrada del ejecutable."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

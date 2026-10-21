"""Interfaz de línea de comandos del robot."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from pydantic import SecretStr

from .captcha.anticaptcha import AntiCaptchaClient
from .captcha.inject import detectar_accion, detectar_sitekey, es_enterprise, es_invisible
from .config import ConfigError, RobotConfig, RobotEntry, cargar_config, validar_ambiente
from .logging import configurar_logging, obtener_logger
from .manifest import extraer_claves
from .models import Periodo, TipoComprobante
from .period import disponibilidad, sugerir_periodo
from .pipeline import Opciones, Robot
from .portal.login import iniciar_sesion
from .portal.received import ConsultaSRIError, navegar_a_recibidos, texto_mensajes
from .portal.selectors import Selectores, primer_visible
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
        typer.Option(
            "--config",
            "-c",
            help="Archivo YAML de configuración. Opcional si pasa --ruc y --password.",
        ),
    ] = Path(CONFIG_POR_DEFECTO),
    selectores: Annotated[
        Path | None,
        typer.Option("--selectors", "-s", help="Archivo YAML de selectores."),
    ] = None,
    periodos: Annotated[
        list[str] | None,
        typer.Option(
            "--periodo",
            "-p",
            help=(
                "Período a descargar en formato YYYY-MM o YYYY-MM-DD. Repetible: "
                "-p 2026-07 -p 2026-08 -p 2026-09. Si se omite, se usa el mes cerrado anterior."
            ),
        ),
    ] = None,
    ruc: Annotated[
        str | None,
        typer.Option("--ruc", "-r", help="RUC (13 dígitos) o cédula (10) del contribuyente."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            "-w",
            help="Clave del portal. Si se omite junto a --ruc, se pregunta de forma oculta.",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Clave de anti-captcha. Opcional: el navegador suele bastar."),
    ] = None,
    perfil: Annotated[
        str | None,
        typer.Option("--perfil", help="Carpeta del perfil de Chrome calentado (ver 'calentar')."),
    ] = None,
    salida: Annotated[
        str | None,
        typer.Option("--salida", help="Carpeta donde guardar los TXT (por defecto ./datos)."),
    ] = None,
    ambiente: Annotated[
        str | None,
        typer.Option("--ambiente", help="Entorno de los datos: dev, test o produccion."),
    ] = None,
    visible: Annotated[
        bool,
        typer.Option("--visible", help="Muestra la ventana de Chrome (necesario para el captcha)."),
    ] = False,
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

    configuracion = _construir_configuracion(
        config,
        ruc=ruc,
        password=_pedir_password(ruc, password),
        api_key=api_key,
        perfil=perfil,
        salida=salida,
        ambiente=ambiente,
        visible=visible,
    )
    catalogo = _cargar_selectores(_ruta_selectores(selectores))

    periodos_objetivo = _resolver_periodos(periodos)
    tipos_objetivo = tuple(TipoComprobante.desde_codigo(codigo) for codigo in (tipos or []))

    for periodo_objetivo in periodos_objetivo:
        estado = disponibilidad(periodo_objetivo.anio, periodo_objetivo.mes, date.today())
        if not estado.disponible:
            typer.secho(
                f"AVISO: el año {periodo_objetivo.anio} no está en el desplegable del portal "
                f"(ofrece {estado.anios_disponibles[-1]}–{estado.anios_disponibles[0]}).",
                fg=typer.colors.YELLOW,
                err=True,
            )

    almacenamiento = crear_almacenamiento(configuracion)
    robot = Robot(configuracion, catalogo, almacenamiento)

    resumen = robot.ejecutar(
        Opciones(
            periodos=periodos_objetivo,
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


@app.command("calentar")
def calentar(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Archivo YAML de configuración."),
    ] = Path(CONFIG_POR_DEFECTO),
    selectores: Annotated[
        Path | None,
        typer.Option("--selectors", "-s", help="Archivo YAML de selectores."),
    ] = None,
    ruc: Annotated[
        str | None,
        typer.Option("--ruc", "-r", help="RUC (13 dígitos) o cédula (10) del contribuyente."),
    ] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", "-w", help="Clave del portal. Si se omite, se pregunta de forma oculta."),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Clave de anti-captcha. Opcional."),
    ] = None,
    perfil: Annotated[
        str | None,
        typer.Option("--perfil", help="Carpeta del perfil de Chrome dedicado."),
    ] = None,
    espera: Annotated[
        int,
        typer.Option("--espera", help="Segundos que se espera la consulta manual."),
    ] = 300,
) -> None:
    """Abre el perfil dedicado para que una persona resuelva el captcha una vez.

    El portal solo acepta tokens de reCAPTCHA de un perfil que haya recibido al
    menos una consulta hecha por una persona. Después de ese clic, el perfil
    queda habilitado y el robot puede consultar todos los períodos sin
    intervención. Ejecute este comando una vez, haga una consulta a mano y
    cierre la ventana cuando el comando termine.
    """
    configurar_logging()
    configuracion = _construir_configuracion(
        config,
        ruc=ruc,
        password=_pedir_password(ruc, password),
        api_key=api_key,
        perfil=perfil,
        salida=None,
        ambiente=None,
        visible=True,
    )
    catalogo = _cargar_selectores(_ruta_selectores(selectores))

    if not configuracion.portal.perfil_dir.strip():
        typer.secho(
            "Indique el perfil con --perfil (por ejemplo ~/.sri-txt-robot/perfil) "
            "o defina 'portal.perfil_dir' en la configuración.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    rucs = configuracion.rucs_activos()
    if ruc:
        rucs = [item for item in rucs if item.ruc == ruc]
    if not rucs:
        typer.secho("No hay RUC configurados para calibrar.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    objetivo = rucs[0]
    portal = configuracion.portal.model_copy(update={"headless": False})

    with Navegador(portal) as navegador:
        pagina = navegador.pagina
        if pagina is None:
            raise typer.Exit(code=1)

        iniciar_sesion(
            pagina,
            objetivo.credenciales,
            catalogo,
            catalogo.valor("login", "url") or portal.login_url,
        )
        navegar_a_recibidos(pagina, catalogo)

        typer.secho(
            "\nSe abrió Chrome con el perfil dedicado.\n"
            "1) Elija un mes cerrado y 'Todos' en el campo día.\n"
            "2) Pulse 'Consultar' a mano y espere los resultados.\n"
            "3) Vuelva a esta terminal: el comando termina solo.\n",
            fg=typer.colors.CYAN,
        )

        limite = time.monotonic() + max(espera, 30)
        while time.monotonic() < limite:
            filas = 0
            tabla = primer_visible(pagina, catalogo.grupo("consulta", "tabla_resultados"), timeout_ms=500)
            if tabla is not None:
                try:
                    filas = tabla.locator("tbody tr").count()
                except Exception:  # noqa: BLE001
                    filas = 0
            if filas or "no existen" in texto_mensajes(pagina, catalogo):
                typer.secho(
                    f"Perfil calentado: la consulta manual devolvió {filas} fila(s). "
                    "Las próximas ejecuciones serán automáticas.",
                    fg=typer.colors.GREEN,
                )
                return
            time.sleep(2)

    typer.secho(
        "No se detectó ninguna consulta manual dentro del plazo. "
        "Vuelva a ejecutar 'calentar' y pulse 'Consultar' en la ventana de Chrome.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command("periodo")
def periodo(
    hoy: Annotated[
        str | None,
        typer.Option("--hoy", help="Fecha de referencia en formato YYYY-MM-DD."),
    ] = None,
) -> None:
    """Muestra el período sugerido y los años que ofrece el portal."""
    fecha = date.fromisoformat(hoy) if hoy else date.today()
    anio, mes = sugerir_periodo(fecha)
    estado = disponibilidad(anio, mes, fecha)

    typer.echo(f"Fecha de referencia:  {fecha.isoformat()}")
    typer.echo(f"Período sugerido:     {anio:04d}-{mes:02d}")
    typer.echo(f"Años en el portal:    {estado.anios_disponibles[-1]}–{estado.anios_disponibles[0]}")
    typer.echo("Ventana de descarga:  no aplica; el portal permite cualquier período de esos años.")


@app.command("calibrar")
def calibrar(
    config: Annotated[
        Path,
        typer.Option("--config", "-c", help="Archivo YAML de configuración."),
    ] = Path(CONFIG_POR_DEFECTO),
    selectores: Annotated[
        Path | None,
        typer.Option("--selectors", "-s", help="Archivo YAML de selectores."),
    ] = None,
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
    catalogo = _cargar_selectores(_ruta_selectores(selectores))

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

        iniciar_sesion(
            pagina,
            objetivo.credenciales,
            catalogo,
            catalogo.valor("login", "url") or configuracion.portal.login_url,
        )

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

        diagnostico["recaptcha"] = {
            "sitekey": detectar_sitekey(pagina),
            "accion": detectar_accion(pagina),
            "enterprise": es_enterprise(pagina),
            "invisible": es_invisible(pagina),
            "campos": (diagnostico.get("recaptcha") or {}).get("campos", 0),
        }

    (salida / "diagnostico.json").write_text(json.dumps(diagnostico, ensure_ascii=False, indent=2), encoding="utf-8")

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


def _ruta_selectores(ruta: Path | None) -> Path:
    """Devuelve el archivo de selectores, respetando SRI_ROBOT_SELECTORS."""
    if ruta is not None:
        return ruta
    return Path(os.environ.get("SRI_ROBOT_SELECTORS", SELECTORES_POR_DEFECTO))


def _pedir_password(ruc: str | None, password: str | None) -> str | None:
    """Devuelve la clave del portal; si hace falta, la pregunta sin mostrarla."""
    if password or not ruc:
        return password
    if not sys.stdin.isatty():
        raise typer.BadParameter("Falta --password y no hay terminal para preguntarla.")
    respuesta: str = typer.prompt(f"Clave del SRI para {ruc}", hide_input=True)
    return respuesta


def _construir_configuracion(
    ruta: Path,
    *,
    ruc: str | None,
    password: str | None,
    api_key: str | None,
    perfil: str | None,
    salida: str | None,
    ambiente: str | None = None,
    visible: bool = False,
) -> RobotConfig:
    """Combina el YAML (si existe) con lo que venga en la línea de comandos.

    Con ``--ruc`` y ``--password`` no hace falta ningún archivo: el robot se
    configura por completo desde el comando.
    """
    try:
        return _configuracion_para(
            ruta,
            ruc=ruc,
            password=password,
            api_key=api_key,
            perfil=perfil,
            salida=salida,
            ambiente=ambiente,
            visible=visible,
        )
    except (ConfigError, ValueError) as error:
        typer.secho(f"Error de configuración: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error


def _configuracion_para(
    ruta: Path,
    *,
    ruc: str | None,
    password: str | None,
    api_key: str | None,
    perfil: str | None,
    salida: str | None,
    ambiente: str | None = None,
    visible: bool = False,
) -> RobotConfig:
    base = _cargar(ruta) if ruta.is_file() else RobotConfig()

    portal = base.portal
    if perfil:
        portal = portal.model_copy(update={"perfil_dir": perfil})
    if visible:
        portal = portal.model_copy(update={"headless": False})

    captcha = base.captcha
    if api_key:
        captcha = captcha.model_copy(update={"api_key": SecretStr(api_key)})

    storage = base.storage
    if ambiente:
        storage = storage.model_copy(update={"ambiente": validar_ambiente(ambiente)})
    if salida:
        storage = storage.model_copy(update={"local_dir": salida})
    elif not storage.bucket and not storage.local_dir:
        storage = storage.model_copy(update={"local_dir": "./datos"})

    robots = base.robots
    if password:
        if not ruc:
            raise ConfigError("Indique --ruc junto con --password.")
        robots = (RobotEntry(ruc=ruc, password=SecretStr(password), activo=True),)
    elif ruc and not any(entrada.ruc == ruc for entrada in robots):
        raise ConfigError(f"El RUC {ruc} no está en {ruta}: agregue --password para usarlo sin editar el YAML.")

    if not robots:
        raise ConfigError("No hay contribuyentes: indique --ruc y --password (o use --config).")

    return base.model_copy(update={"portal": portal, "captcha": captcha, "storage": storage, "robots": robots})


def _cargar_selectores(ruta: Path) -> Selectores:
    try:
        return Selectores.cargar(ruta)
    except (FileNotFoundError, ValueError) as error:
        typer.secho(f"Error de selectores: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from error


def _resolver_periodos(valores: list[str] | None) -> tuple[Periodo, ...]:
    """Convierte los períodos de la línea de comandos en objetos de dominio.

    Admite valores repetidos (``-p 2026-07 -p 2026-08``) y listas separadas
    por comas (``-p 2026-07,2026-08``), sin duplicados y en el orden indicado.
    Sin valores se usa el mes cerrado anterior.
    """
    if not valores:
        anio, mes = sugerir_periodo(date.today())
        return (Periodo(anio=anio, mes=mes),)

    periodos: list[Periodo] = []
    vistos: set[str] = set()
    for valor in valores:
        for parte in valor.split(","):
            texto = parte.strip()
            if not texto:
                continue
            periodo = Periodo.desde_texto(texto)
            if periodo.clave in vistos:
                continue
            vistos.add(periodo.clave)
            periodos.append(periodo)

    if not periodos:
        anio, mes = sugerir_periodo(date.today())
        return (Periodo(anio=anio, mes=mes),)

    return tuple(periodos)


def main() -> None:
    """Punto de entrada del ejecutable."""
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()

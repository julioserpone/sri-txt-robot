"""Carga y validación de la configuración del robot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .models import ConfigRuc, Credenciales, TipoComprobante

RUC_PATTERN = r"^\d{10}$|^\d{13}$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaptchaConfig(_Base):
    """Parámetros del resolvedor de reCAPTCHA (anti-captcha)."""

    api_url: str = "https://api.anti-captcha.com"
    api_key_env: str = "ANTICAPTCHA_API_KEY"
    api_key: SecretStr | None = None
    task_types: tuple[str, ...] = (
        "RecaptchaV2EnterpriseTaskProxyless",
        "RecaptchaV2TaskProxyless",
        "RecaptchaV3EnterpriseTaskProxyless",
        "RecaptchaV3TaskProxyless",
    )
    poll_interval_seconds: float = 3.0
    poll_timeout_seconds: float = 180.0
    report_outcomes: bool = True

    def resolver_api_key(self) -> str:
        """Devuelve la clave del servicio, priorizando la variable de entorno."""
        if self.api_key is not None:
            return self.api_key.get_secret_value()
        valor = os.environ.get(self.api_key_env, "").strip()
        if not valor:
            raise ConfigError(
                f"Falta la clave de anti-captcha. Defina {self.api_key_env} o 'captcha.api_key'."
            )
        return valor


class PortalConfig(_Base):
    """Parámetros de navegación en el portal del SRI."""

    base_url: str = "https://srienlinea.sri.gob.ec"
    login_url: str = "https://srienlinea.sri.gob.ec/tuportal-internet/"
    headless: bool = True
    timeout_ms: int = 45_000
    navigation_timeout_ms: int = 60_000
    locale: str = "es-EC"
    timezone: str = "America/Guayaquil"
    user_agent: str = ""
    viewport_width: int = 1440
    viewport_height: int = 900
    download_dir: str = ""


class StorageConfig(_Base):
    """Destino de los TXT descargados."""

    bucket: str = ""
    prefix: str = "sri/txt"
    local_dir: str = ""


class RobotEntry(_Base):
    """Un contribuyente a procesar."""

    ruc: str = Field(pattern=RUC_PATTERN)
    password: SecretStr | None = None
    password_env: str = ""
    clave_adicional: str = ""
    etiqueta: str = ""
    tipos: tuple[str, ...] = ()
    activo: bool = True

    @field_validator("tipos", mode="before")
    @classmethod
    def _normalizar_tipos(cls, valor: Any) -> Any:
        if valor is None:
            return ()
        if isinstance(valor, str):
            valor = [valor]
        return tuple(str(item) for item in valor)

    def a_config_ruc(self) -> ConfigRuc:
        """Convierte la entrada de configuración en un :class:`ConfigRuc`."""
        if self.password is not None:
            contrasena = self.password.get_secret_value()
        elif self.password_env:
            contrasena = os.environ.get(self.password_env, "")
            if not contrasena:
                raise ConfigError(
                    f"La variable {self.password_env} no está definida para el RUC {self.ruc}."
                )
        else:
            raise ConfigError(f"El RUC {self.ruc} no define 'password' ni 'password_env'.")

        credenciales = Credenciales(
            ruc=self.ruc,
            password=contrasena,
            clave_adicional=self.clave_adicional,
        )

        tipos = tuple(TipoComprobante.desde_codigo(codigo) for codigo in self.tipos)

        return ConfigRuc(
            ruc=self.ruc,
            credenciales=credenciales,
            tipos=tipos,
            activo=self.activo,
            etiqueta=self.etiqueta,
        )


class RobotConfig(_Base):
    """Configuración completa del robot."""

    version: int = 1
    captcha: CaptchaConfig = CaptchaConfig()
    portal: PortalConfig = PortalConfig()
    storage: StorageConfig = StorageConfig()
    robots: tuple[RobotEntry, ...] = ()

    def rucs_activos(self) -> list[ConfigRuc]:
        return [entrada.a_config_ruc() for entrada in self.robots if entrada.activo]


class ConfigError(RuntimeError):
    """Error de configuración del robot."""


def cargar_config(path: str | Path) -> RobotConfig:
    """Carga la configuración desde un archivo YAML.

    Acepta variables de entorno en la forma ``${NOMBRE}`` dentro del YAML para
    no escribir secretos en disco.
    """
    ruta = Path(path)
    if not ruta.is_file():
        raise ConfigError(f"No existe el archivo de configuración: {ruta}")

    contenido = ruta.read_text(encoding="utf-8")
    datos = _expandir_variables(contenido)
    return RobotConfig.model_validate(datos)


def _expandir_variables(contenido: str) -> dict[str, Any]:
    """Sustituye ``${NOMBRE}`` por variables de entorno antes de validar."""
    import re

    def reemplazar(marco: re.Match[str]) -> str:
        nombre = marco.group(1)
        if nombre not in os.environ:
            raise ConfigError(f"La variable de entorno {nombre} no está definida.")
        return os.environ[nombre]

    texto = re.sub(r"\$\{([A-Z0-9_]+)\}", reemplazar, contenido)
    datos = yaml.safe_load(texto) or {}
    if not isinstance(datos, dict):
        raise ConfigError("El archivo de configuración debe contener un mapa YAML.")
    return datos

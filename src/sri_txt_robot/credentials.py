"""Credenciales selladas para las invocaciones de AWS Lambda.

El emisor (por ejemplo ``autotributo``) cifra un JSON con la clave **pública**
del robot usando ``sodium_crypto_box_seal``. Solo la Lambda, que custodia la
clave privada en una variable de entorno, puede abrirlo. Así la contraseña del
portal no viaja en claro en el evento y no hace falta AWS Secrets Manager.

El sobre contiene::

    {
      "ruc": "1758871873001",
      "password": "...",
      "claveAdicional": "",
      "iat": 1760000000
    }

``ruc`` se repite dentro del sobre para que el robot compruebe que las
credenciales corresponden al RUC del evento. ``iat`` permite rechazar sobres
demasiado antiguos; las invocaciones asíncronas de Lambda pueden reintentarse
durante varios minutos, por eso la tolerancia por defecto es amplia.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from collections.abc import Iterable
from typing import Any

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

#: Variable de entorno con la clave privada actual, en base64.
ENV_CLAVE_PRIVADA = "SRI_CREDENTIALS_PRIVATE_KEY"

#: Variable opcional con la clave privada anterior, para rotar sin downtime.
ENV_CLAVE_PRIVADA_ANTERIOR = "SRI_CREDENTIALS_PRIVATE_KEY_ANTERIOR"

#: Antigüedad máxima aceptada para un sobre, en segundos. ``0`` la desactiva.
ENV_MAX_EDAD = "SRI_CREDENTIALS_MAX_AGE_SECONDS"

#: Lambda reintenta los eventos asíncronos durante un máximo de 6 horas.
MAX_EDAD_POR_DEFECTO = 24 * 60 * 60

#: Margen para relojes ligeramente adelantados.
MARGEN_RELOJ_SEGUNDOS = 5 * 60

#: Un sobre real ronda los 200-300 bytes. El límite evita payloads abusivos.
MAX_SELLO_BYTES = 8_192


class CredencialesError(RuntimeError):
    """El sobre de credenciales no se puede abrir o no es válido."""


class CredencialesSelladas(BaseModel):
    """Contenido en claro del sobre de credenciales."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    ruc: str = Field(pattern=r"^\d{10}$|^\d{13}$")
    password: SecretStr = Field(min_length=1)
    clave_adicional: str = Field(default="", alias="claveAdicional")
    iat: int | None = None

    @field_validator("clave_adicional", mode="before")
    @classmethod
    def _normalizar_clave_adicional(cls, valor: Any) -> str:
        return "" if valor is None else str(valor)


def generar_par_claves() -> tuple[str, str]:
    """Genera un par de claves y devuelve ``(pública, privada)`` en base64."""
    privada = PrivateKey.generate()
    return _b64(bytes(privada.public_key)), _b64(bytes(privada))


def sellar_credenciales(
    *,
    ruc: str,
    password: str,
    public_key_b64: str,
    clave_adicional: str = "",
    iat: int | None = None,
) -> str:
    """Cifra las credenciales con la clave pública y devuelve base64.

    Se usa en pruebas y en el comando ``sellar-credenciales``. En producción
    el sellado lo hace la aplicación PHP con ``sodium_crypto_box_seal``.
    """
    if not password:
        raise CredencialesError("La contraseña del portal no puede estar vacía.")

    try:
        credenciales = CredencialesSelladas(
            ruc=ruc,
            password=SecretStr(password),
            claveAdicional=clave_adicional,
            iat=int(time.time()) if iat is None else iat,
        )
    except ValidationError as error:
        raise CredencialesError("Los datos de la credencial no son válidos.") from error
    publica = _leer_clave_publica(public_key_b64)
    contenido = json.dumps(
        {
            "ruc": credenciales.ruc,
            "password": credenciales.password.get_secret_value(),
            "claveAdicional": credenciales.clave_adicional,
            "iat": credenciales.iat,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    return _b64(SealedBox(publica).encrypt(contenido))


def abrir_credenciales(
    sealed_b64: str,
    private_keys_b64: str | Iterable[str],
    *,
    max_age_seconds: int | None = MAX_EDAD_POR_DEFECTO,
    ahora: float | None = None,
) -> CredencialesSelladas:
    """Descifra y valida un sobre de credenciales.

    ``private_keys_b64`` puede ser una clave o una colección de claves. Se
    prueban en orden, lo que permite rotar la clave privada manteniendo la
    anterior durante un periodo de gracia.
    """
    texto = sealed_b64.strip()
    if not texto:
        raise CredencialesError("El campo 'sealed_credentials' está vacío.")
    if len(texto) > MAX_SELLO_BYTES:
        raise CredencialesError("El campo 'sealed_credentials' es demasiado grande.")

    claves = _normalizar_claves(private_keys_b64)
    if not claves:
        raise CredencialesError(
            f"Falta la clave privada del robot. Defina {ENV_CLAVE_PRIVADA} (o {ENV_CLAVE_PRIVADA_ANTERIOR})."
        )

    sellado = _decodificar_b64(texto, "sealed_credentials")
    contenido: bytes | None = None
    ultimo_error: Exception | None = None

    for clave in claves:
        try:
            llave = _leer_clave_privada(clave)
        except CredencialesError as error:
            # Una clave mal formada no debe impedir probar la anterior durante
            # una rotación.
            ultimo_error = error
            continue

        try:
            contenido = SealedBox(llave).decrypt(sellado)
            break
        except (CryptoError, ValueError) as error:
            ultimo_error = error

    if contenido is None:
        if isinstance(ultimo_error, CredencialesError):
            raise ultimo_error
        raise CredencialesError(
            "No se pudo descifrar 'sealed_credentials'. Compruebe que la clave pública "
            "de la aplicación corresponde a la clave privada de la Lambda."
        ) from ultimo_error

    try:
        datos: Any = json.loads(contenido)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CredencialesError("El sobre descifrado no contiene JSON válido.") from error

    if not isinstance(datos, dict):
        raise CredencialesError("El sobre descifrado debe ser un objeto JSON.")

    try:
        credenciales = CredencialesSelladas.model_validate(datos)
    except ValidationError as error:
        raise CredencialesError("El sobre descifrado no tiene el formato esperado.") from error

    _validar_iat(credenciales.iat, max_age_seconds=max_age_seconds, ahora=ahora)
    return credenciales


def claves_privadas_desde_entorno() -> tuple[str, ...]:
    """Devuelve la clave privada actual y, si existe, la anterior."""
    valores = (
        os.environ.get(ENV_CLAVE_PRIVADA, ""),
        os.environ.get(ENV_CLAVE_PRIVADA_ANTERIOR, ""),
    )
    return tuple(valor.strip() for valor in valores if valor.strip())


def max_edad_desde_entorno() -> int | None:
    """Lee ``SRI_CREDENTIALS_MAX_AGE_SECONDS``; ``0`` desactiva la validación."""
    crudo = os.environ.get(ENV_MAX_EDAD, str(MAX_EDAD_POR_DEFECTO)).strip()
    try:
        valor = int(crudo)
    except ValueError as error:
        raise CredencialesError(f"{ENV_MAX_EDAD} debe ser un número entero de segundos.") from error
    return None if valor <= 0 else valor


def _normalizar_claves(private_keys_b64: str | Iterable[str]) -> tuple[str, ...]:
    valores: tuple[str, ...] = (
        (private_keys_b64,) if isinstance(private_keys_b64, str) else tuple(private_keys_b64)
    )
    return tuple(valor.strip() for valor in valores if valor.strip())


def _validar_iat(iat: int | None, *, max_age_seconds: int | None, ahora: float | None) -> None:
    if iat is None or max_age_seconds is None:
        return

    instante = time.time() if ahora is None else ahora
    if iat > instante + MARGEN_RELOJ_SEGUNDOS:
        raise CredencialesError("El sobre de credenciales tiene una marca de tiempo futura.")
    if instante - iat > max_age_seconds:
        raise CredencialesError(
            "El sobre de credenciales ha caducado. Genere uno nuevo o ajuste "
            f"{ENV_MAX_EDAD}."
        )


def _leer_clave_publica(valor: str) -> PublicKey:
    try:
        return PublicKey(_decodificar_b64(valor, "clave pública"))
    except (ValueError, TypeError, binascii.Error) as error:
        raise CredencialesError("La clave pública del robot no es válida.") from error


def _leer_clave_privada(valor: str) -> PrivateKey:
    try:
        return PrivateKey(_decodificar_b64(valor, "clave privada"))
    except (ValueError, TypeError, binascii.Error) as error:
        raise CredencialesError("La clave privada del robot no es válida.") from error


def _decodificar_b64(valor: str, nombre: str) -> bytes:
    try:
        return base64.b64decode(valor.strip(), validate=True)
    except (binascii.Error, ValueError) as error:
        raise CredencialesError(f"{nombre} no es base64 válido.") from error


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")

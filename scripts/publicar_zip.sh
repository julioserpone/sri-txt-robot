#!/usr/bin/env bash
# Publica el robot como función ZIP: el código y las dependencias Python viajan
# en el paquete; Chromium, Xvfb y el perfil calentado se sirven desde S3 y se
# extraen en /tmp solo cuando hay descargas pendientes.
#
# Sustituye a scripts/publicar.sh (imagen de contenedor) para eliminar ECR.
# No crea ni modifica la función de imagen: use --funcion para desplegar una
# función de prueba antes del cutover.
#
# Requisitos: haber ejecutado scripts/construir_zip.sh y tener credenciales de
# AWS con permisos de Lambda, S3 y (para crear) IAM.
#
# Uso:
#   scripts/publicar_zip.sh --funcion sri-txt-robot-zip
#   scripts/publicar_zip.sh --funcion sri-txt-robot --actualizar
#
# El secreto del callback se lee de un archivo para no dejarlo en el historial:
#   scripts/publicar_zip.sh --callback-url https://app/webhooks/sri-robot \
#     --callback-secret-file ~/.sri-txt-robot/callback.secret
set -euo pipefail

cd "$(dirname "$0")/.."

FUNCION="sri-txt-robot-zip"
FUNCION_ORIGEN="sri-txt-robot"
REGION="${AWS_REGION:-us-east-2}"
PERFIL="${AWS_PROFILE:-}"
AMBIENTE="produccion"
BUCKET=""
ROL=""
MEMORIA=3008
TIMEOUT=900
ACTUALIZAR=0
CALLBACK_URL=""
CALLBACK_SECRET_FILE=""
CALLBACK_HOSTS=""
PRIVADA_ARCHIVO=""
ARCHIVO_ENTORNO=""

uso() {
    cat <<'TEXTO'
Publica el robot como función Lambda ZIP.

Opciones:
  --funcion NOMBRE       Función destino (sri-txt-robot-zip).
  --funcion-origen NOMBRE
                         Función de la que se reutilizan rol y clave privada
                         si no se indican (sri-txt-robot).
  --region REGION        Región de AWS (AWS_REGION o us-east-2).
  --perfil NOMBRE        Perfil de AWS CLI (AWS_PROFILE).
  --ambiente NOMBRE      Ambiente de los datos (produccion).
  --bucket NOMBRE        Bucket para TXT y runtime. Por defecto
                         sri-txt-robot-<cuenta>-<ambiente>.
  --rol ARN              Rol de ejecución. Por defecto, el de --funcion-origen.
  --memoria MB           Memoria de la función (3008).
  --timeout SEGUNDOS     Tiempo máximo de la función (900).
  --private-key-file RUTA
                         Archivo con SRI_CREDENTIALS_PRIVATE_KEY. Si se omite,
                         se lee de la función existente.
  --callback-url URL     URL del callback firmado.
  --callback-secret-file RUTA
                         Archivo con el secreto del callback (mínimo 16 chars).
  --callback-hosts LISTA Hosts autorizados para callback_url del evento.
  --env-file RUTA        Variables de entorno adicionales (KEY=VALUE por
                         línea). Útil para ANTICAPTCHA_API_KEY u otros
                         secretos que no deben viajar en el comando.
  --actualizar           Actualiza una función existente.
  -h, --help             Muestra esta ayuda.
TEXTO
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --funcion) FUNCION="$2"; shift 2 ;;
        --funcion-origen) FUNCION_ORIGEN="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --perfil) PERFIL="$2"; shift 2 ;;
        --ambiente) AMBIENTE="$2"; shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        --rol) ROL="$2"; shift 2 ;;
        --memoria) MEMORIA="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --private-key-file) PRIVADA_ARCHIVO="$2"; shift 2 ;;
        --callback-url) CALLBACK_URL="$2"; shift 2 ;;
        --callback-secret-file) CALLBACK_SECRET_FILE="$2"; shift 2 ;;
        --callback-hosts) CALLBACK_HOSTS="$2"; shift 2 ;;
        --env-file) ARCHIVO_ENTORNO="$2"; shift 2 ;;
        --actualizar) ACTUALIZAR=1; shift ;;
        -h | --help) uso; exit 0 ;;
        *) echo "Opción desconocida: $1" >&2; uso >&2; exit 2 ;;
    esac
done

AWS=(aws --region "$REGION")
if [[ -n "$PERFIL" ]]; then
    AWS+=(--profile "$PERFIL")
fi

if [[ ! -f dist/funcion.zip ]]; then
    echo "Falta dist/funcion.zip. Ejecute scripts/construir_zip.sh." >&2
    exit 2
fi

CUENTA="$("${AWS[@]}" sts get-caller-identity --query Account --output text)"
if [[ -z "$BUCKET" ]]; then
    BUCKET="sri-txt-robot-${CUENTA}-${AMBIENTE}"
fi

if [[ -z "$ROL" ]]; then
    ROL="$("${AWS[@]}" lambda get-function-configuration \
        --function-name "$FUNCION_ORIGEN" --query Role --output text)"
fi

PRIVADA_TEMPORAL=0
if [[ -z "$PRIVADA_ARCHIVO" ]] && [[ -n "${SRI_CREDENTIALS_PRIVATE_KEY:-}" ]]; then
    PRIVADA_ARCHIVO="$(mktemp)"
    PRIVADA_TEMPORAL=1
    printf '%s' "$SRI_CREDENTIALS_PRIVATE_KEY" > "$PRIVADA_ARCHIVO"
fi

CLAVE_PRIVADA=""
if [[ -n "$PRIVADA_ARCHIVO" ]]; then
    if [[ ! -f "$PRIVADA_ARCHIVO" ]]; then
        echo "No existe el archivo de clave privada: $PRIVADA_ARCHIVO" >&2
        exit 2
    fi
    CLAVE_PRIVADA="$(tr -d '[:space:]' < "$PRIVADA_ARCHIVO")"
else
    CLAVE_PRIVADA="$("${AWS[@]}" lambda get-function-configuration \
        --function-name "$FUNCION" \
        --query 'Environment.Variables.SRI_CREDENTIALS_PRIVATE_KEY' \
        --output text 2>/dev/null || true)"
    if [[ -z "$CLAVE_PRIVADA" || "$CLAVE_PRIVADA" == "None" ]]; then
        CLAVE_PRIVADA="$("${AWS[@]}" lambda get-function-configuration \
            --function-name "$FUNCION_ORIGEN" \
            --query 'Environment.Variables.SRI_CREDENTIALS_PRIVATE_KEY' \
            --output text)"
    fi
fi

if [[ -z "$CLAVE_PRIVADA" || "$CLAVE_PRIVADA" == "None" ]]; then
    echo "Falta SRI_CREDENTIALS_PRIVATE_KEY. Use --private-key-file." >&2
    exit 2
fi

CALLBACK_SECRET=""
if [[ -n "$CALLBACK_SECRET_FILE" ]]; then
    if [[ ! -f "$CALLBACK_SECRET_FILE" ]]; then
        echo "No existe el archivo del secreto: $CALLBACK_SECRET_FILE" >&2
        exit 2
    fi
    CALLBACK_SECRET="$(tr -d '[:space:]' < "$CALLBACK_SECRET_FILE")"
elif [[ -n "${SRI_CALLBACK_SECRET:-}" ]]; then
    CALLBACK_SECRET="$SRI_CALLBACK_SECRET"
fi

if [[ -n "$CALLBACK_URL" && ${#CALLBACK_SECRET} -lt 16 ]]; then
    echo "--callback-url requiere --callback-secret-file (mínimo 16 caracteres)." >&2
    exit 2
fi

if [[ -n "$ARCHIVO_ENTORNO" && ! -f "$ARCHIVO_ENTORNO" ]]; then
    echo "No existe el archivo de variables: $ARCHIVO_ENTORNO" >&2
    exit 2
fi

BUNDLE=""
for archivo in dist/runtime/browser-*.tar.gz; do
    if [[ -f "$archivo" ]]; then
        BUNDLE="$archivo"
    fi
done
if [[ -z "$BUNDLE" ]]; then
    echo "Falta el bundle del navegador. Ejecute scripts/construir_zip.sh." >&2
    exit 2
fi
BUNDLE_KEY="_runtime/$(basename "$BUNDLE")"
BUNDLE_SHA="$(awk '{print $1}' "$BUNDLE.sha256")"
PERFIL="dist/runtime/perfil.tar.gz"
PERFIL_KEY="_runtime/perfil.tar.gz"
PERFIL_SHA="$(awk '{print $1}' "$PERFIL.sha256")"

# El ZIP supera los 50 MB, así que Lambda rechaza el envío directo (el cuerpo
# base64 supera el límite de 70 MB). Se publica en S3 y se referencia por clave.
CODIGO="dist/funcion.zip"
CODIGO_SHA="$( { shasum -a 256 "$CODIGO" 2>/dev/null || sha256sum "$CODIGO"; } | awk '{print $1}')"
CODIGO_KEY="_runtime/funcion-${CODIGO_SHA}.zip"

echo "== subiendo runtime a s3://$BUCKET =="
"${AWS[@]}" s3 cp "$BUNDLE" "s3://$BUCKET/$BUNDLE_KEY" --only-show-errors
"${AWS[@]}" s3 cp "$PERFIL" "s3://$BUCKET/$PERFIL_KEY" --only-show-errors
"${AWS[@]}" s3 cp "$CODIGO" "s3://$BUCKET/$CODIGO_KEY" --only-show-errors

ENTORNO="$(mktemp)"
EXISTENTE_ENV=""
limpiar() {
    rm -f "$ENTORNO"
    if [[ -n "$EXISTENTE_ENV" ]]; then
        rm -f "$EXISTENTE_ENV"
    fi
    if [[ "$PRIVADA_TEMPORAL" -eq 1 ]]; then
        rm -f "$PRIVADA_ARCHIVO"
    fi
}
trap limpiar EXIT

EXISTE_FUNCION=0
if "${AWS[@]}" lambda get-function --function-name "$FUNCION" >/dev/null 2>&1; then
    EXISTE_FUNCION=1
    if [[ "$ACTUALIZAR" -eq 0 ]]; then
        echo "La función $FUNCION ya existe. Use --actualizar para redesplegarla." >&2
        exit 1
    fi
    # Al actualizar, se conservan las variables que no vienen en los argumentos
    # (callback firmado, claves propias, etc.). Antes se reemplazaba todo el
    # entorno y un redespliegue sin --callback-url borraba el callback.
    EXISTENTE_ENV="$(mktemp)"
    "${AWS[@]}" lambda get-function-configuration \
        --function-name "$FUNCION" \
        --query 'Environment.Variables' \
        --output json > "$EXISTENTE_ENV"
fi

SRI_PRIVADA="$CLAVE_PRIVADA" \
SRI_CALLBACK_URL_ENV="$CALLBACK_URL" \
SRI_CALLBACK_SECRET_ENV="$CALLBACK_SECRET" \
SRI_CALLBACK_HOSTS_ENV="$CALLBACK_HOSTS" \
SRI_BUCKET_ENV="$BUCKET" \
SRI_AMBIENTE_ENV="$AMBIENTE" \
SRI_BUNDLE_KEY_ENV="$BUNDLE_KEY" \
SRI_BUNDLE_SHA_ENV="$BUNDLE_SHA" \
SRI_PERFIL_KEY_ENV="$PERFIL_KEY" \
SRI_PERFIL_SHA_ENV="$PERFIL_SHA" \
SRI_EXTRA_ENV_FILE_ENV="$ARCHIVO_ENTORNO" \
SRI_EXISTENTE_ENV_FILE_ENV="$EXISTENTE_ENV" \
python3 - "$ENTORNO" <<'PY'
import json
import os
import sys

# Variables que ya tenía la función y que este despliegue no especifica.
variables: dict[str, str] = {}
existente = os.environ.get("SRI_EXISTENTE_ENV_FILE_ENV", "").strip()
if existente:
    with open(existente, encoding="utf-8") as archivo:
        variables.update(json.load(archivo) or {})

variables.update(
    {
        "SRI_BUCKET": os.environ["SRI_BUCKET_ENV"],
        "SRI_AMBIENTE": os.environ["SRI_AMBIENTE_ENV"],
        "SRI_ROBOT_CONFIG": "/var/task/config/robots.yaml",
        "SRI_ROBOT_SELECTORS": "/var/task/config/selectors.yaml",
        "SRI_CREDENTIALS_PRIVATE_KEY": os.environ["SRI_PRIVADA"],
        "SRI_CREDENTIALS_MAX_AGE_SECONDS": "86400",
        "SRI_RUNTIME_BUCKET": os.environ["SRI_BUCKET_ENV"],
        "SRI_RUNTIME_BROWSER_KEY": os.environ["SRI_BUNDLE_KEY_ENV"],
        "SRI_RUNTIME_BROWSER_SHA256": os.environ["SRI_BUNDLE_SHA_ENV"],
        "SRI_RUNTIME_PERFIL_KEY": os.environ["SRI_PERFIL_KEY_ENV"],
        "SRI_RUNTIME_PERFIL_SHA256": os.environ["SRI_PERFIL_SHA_ENV"],
        "DISPLAY": ":99",
        "SRI_ASEGURAR_XVFB": "1",
        "SRI_CHROME_BANDERAS": "--no-zygote",
    }
)

if os.environ.get("SRI_CALLBACK_URL_ENV"):
    variables["SRI_CALLBACK_URL"] = os.environ["SRI_CALLBACK_URL_ENV"]
    variables["SRI_CALLBACK_SECRET"] = os.environ["SRI_CALLBACK_SECRET_ENV"]
if os.environ.get("SRI_CALLBACK_HOSTS_ENV"):
    variables["SRI_CALLBACK_ALLOWED_HOSTS"] = os.environ["SRI_CALLBACK_HOSTS_ENV"]

extra = os.environ.get("SRI_EXTRA_ENV_FILE_ENV", "").strip()
if extra:
    with open(extra, encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
                valor = valor[1:-1]
            if clave:
                variables[clave] = valor

with open(sys.argv[1], "w", encoding="utf-8") as archivo:
    json.dump({"Variables": variables}, archivo)
PY

if [[ "$EXISTE_FUNCION" -eq 1 ]]; then
    echo "== actualizando $FUNCION =="
    "${AWS[@]}" lambda update-function-code \
        --function-name "$FUNCION" \
        --s3-bucket "$BUCKET" \
        --s3-key "$CODIGO_KEY" \
        --output text >/dev/null
    "${AWS[@]}" lambda wait function-updated --function-name "$FUNCION"
    "${AWS[@]}" lambda update-function-configuration \
        --function-name "$FUNCION" \
        --runtime python3.14 \
        --handler sri_txt_robot.handler.handler \
        --memory-size "$MEMORIA" \
        --timeout "$TIMEOUT" \
        --environment "file://$ENTORNO" \
        --output text >/dev/null
    "${AWS[@]}" lambda wait function-updated --function-name "$FUNCION"
else
    echo "== creando $FUNCION =="
    "${AWS[@]}" lambda create-function \
        --function-name "$FUNCION" \
        --runtime python3.14 \
        --architectures arm64 \
        --handler sri_txt_robot.handler.handler \
        --role "$ROL" \
        --code "S3Bucket=$BUCKET,S3Key=$CODIGO_KEY" \
        --memory-size "$MEMORIA" \
        --timeout "$TIMEOUT" \
        --ephemeral-storage "Size=2048" \
        --environment "file://$ENTORNO" \
        --description "sri-txt-robot ZIP: navegador y perfil en S3" \
        --output text >/dev/null
    "${AWS[@]}" lambda wait function-active --function-name "$FUNCION"
fi

echo "== listo: $FUNCION ($REGION) =="
"${AWS[@]}" lambda get-function-configuration \
    --function-name "$FUNCION" \
    --query '{Estado:State,PackageType:PackageType,Memoria:MemorySize,Timeout:Timeout,Arquitecturas:Architectures,BytesCodigo:CodeSize}' \
    --output table

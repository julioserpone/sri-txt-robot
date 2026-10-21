#!/usr/bin/env bash
# Publica el robot en AWS Lambda (imagen de contenedor) con EventBridge mensual.
#
# - Idempotente: si la función ya existe no hace nada salvo que pase --actualizar.
# - Sin AWS Secrets Manager: la clave del portal va como variable de entorno de
#   la función (cifrada en reposo por Lambda).
# - Recursos mínimos: bucket S3 + repositorio ECR + rol IAM + función + regla.
#
# Uso:
#   scripts/publicar.sh --ruc 1758871873001 --password '...' --ambiente produccion
#
# Antes de ejecutarlo, el perfil de Chrome debe estar "calentado" en esta
# máquina (sri-txt-robot calentar) porque se copia dentro de la imagen.
set -euo pipefail

cd "$(dirname "$0")/.."

FUNCION="sri-txt-robot"
REGION="${AWS_REGION:-us-east-1}"
AMBIENTE="dev"
BUCKET=""
RUC=""
PASSWORD=""
ANTICAPTCHA=""
TIPOS="01"
PERFIL="$HOME/.sri-txt-robot/perfil"
CRON="cron(0 8 5 * ? *)"
MEMORIA=2048
TIMEOUT=900
ACTUALIZAR=0
ROL="sri-txt-robot-lambda"

uso() {
    cat <<'TEXTO'
Publica el robot del SRI en AWS Lambda.

Opciones:
  --ruc RUC             RUC (13 dígitos) o cédula (10) del contribuyente.
  --password CLAVE      Clave del portal. Si se omite, se pregunta oculta.
  --anticaptcha-key K   Clave de anti-captcha (recomendada en Lambda: el
                        perfil calentado no siempre vale en otro sistema).
  --ambiente NOMBRE     dev (por defecto), test o produccion.
  --bucket NOMBRE       Bucket S3. Por defecto sri-txt-robot-<cuenta>-<ambiente>.
  --tipos LISTA         Tipos separados por comas (por defecto 01).
  --perfil RUTA         Perfil de Chrome calentado (~/.sri-txt-robot/perfil).
  --funcion NOMBRE      Nombre de la función de Lambda (sri-txt-robot).
  --region REGION       Región de AWS (AWS_REGION o us-east-1).
  --cron EXPRESION      Frecuencia de EventBridge (mensual por defecto).
  --memoria MB          Memoria de la función (2048).
  --timeout SEGUNDOS    Tiempo máximo de la función (900).
  --actualizar          Vuelve a desplegar aunque la función ya exista.
  -h, --help            Muestra esta ayuda.
TEXTO
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ruc) RUC="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --anticaptcha-key) ANTICAPTCHA="$2"; shift 2 ;;
        --ambiente) AMBIENTE="$2"; shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        --tipos) TIPOS="$2"; shift 2 ;;
        --perfil) PERFIL="$2"; shift 2 ;;
        --funcion) FUNCION="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --cron) CRON="$2"; shift 2 ;;
        --memoria) MEMORIA="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --actualizar) ACTUALIZAR=1; shift ;;
        -h | --help) uso; exit 0 ;;
        *) echo "Opción desconocida: $1" >&2; uso >&2; exit 2 ;;
    esac
done

if ! [[ "$RUC" =~ ^[0-9]{10}([0-9]{3})?$ ]]; then
    echo "Indique un RUC o cédula válidos con --ruc." >&2
    exit 2
fi

if ! [[ "$AMBIENTE" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "Ambiente inválido: '$AMBIENTE' (use dev, test o produccion)." >&2
    exit 2
fi

if [[ -z "$PASSWORD" ]]; then
    if [[ ! -t 0 ]]; then
        echo "Falta --password y no hay terminal para preguntarla." >&2
        exit 2
    fi
    read -r -s -p "Clave del SRI para $RUC: " PASSWORD
    echo
fi

for herramienta in aws docker rsync python3; do
    command -v "$herramienta" >/dev/null 2>&1 || {
        echo "Falta '$herramienta'. Instálelo antes de publicar." >&2
        exit 2
    }
done

if [[ ! -d "$PERFIL" ]]; then
    echo "No existe el perfil calentado en $PERFIL." >&2
    echo "Ejecute antes: sri-txt-robot calentar --perfil \"$PERFIL\"" >&2
    exit 2
fi

echo "== Comprobando credenciales de AWS =="
CUENTA="$(aws sts get-caller-identity --query Account --output text --region "$REGION")" || {
    echo "No se pudo autenticar con AWS. Renueve sus credenciales (aws sso login / variables)." >&2
    exit 2
}
echo "Cuenta $CUENTA, región $REGION"

BUCKET="${BUCKET:-sri-txt-robot-${CUENTA}-${AMBIENTE}}"
ARN_FUNCION="arn:aws:lambda:${REGION}:${CUENTA}:function:${FUNCION}"

# La imagen se construye para la arquitectura de esta máquina; la función debe
# usar la misma.
if [[ "$(uname -m)" == "arm64" || "$(uname -m)" == "aarch64" ]]; then
    ARQUITECTURA="arm64"
else
    ARQUITECTURA="x86_64"
fi

# -- ¿Ya está publicado? ---------------------------------------------------
if aws lambda get-function --function-name "$FUNCION" --region "$REGION" >/dev/null 2>&1; then
    ESTADO="$(aws lambda get-function-configuration --function-name "$FUNCION" --region "$REGION" \
        --query '[State,LastUpdateStatus,Code.ImageUri]' --output text)"
    echo
    echo "El robot YA está publicado: $FUNCION"
    echo "  estado / última actualización / imagen: $ESTADO"
    if [[ "$ACTUALIZAR" -eq 0 ]]; then
        echo
        echo "No se cambió nada. Para desplegar una versión nueva: --actualizar"
        exit 0
    fi
    echo "Se actualizará la función (--actualizar)."
fi

# -- Bucket S3 -------------------------------------------------------------
echo
echo "== Bucket S3: $BUCKET =="
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
    echo "Ya existe."
else
    if [[ "$REGION" == "us-east-1" ]]; then
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
    else
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
            --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
    fi
    echo "Creado."
fi
aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" >/dev/null
aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$REGION" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null

# -- Repositorio ECR -------------------------------------------------------
echo
echo "== Repositorio ECR: $FUNCION =="
if ! aws ecr describe-repositories --repository-names "$FUNCION" --region "$REGION" >/dev/null 2>&1; then
    aws ecr create-repository --repository-name "$FUNCION" --region "$REGION" \
        --image-scanning-configuration scanOnPush=true >/dev/null
    echo "Creado."
else
    echo "Ya existe."
fi
REGISTRO="${CUENTA}.dkr.ecr.${REGION}.amazonaws.com"
IMAGEN="${REGISTRO}/${FUNCION}:${AMBIENTE}"

# -- Contexto de la imagen -------------------------------------------------
echo
echo "== Preparando la imagen (código, configuración y perfil calentado) =="
rm -rf deploy/build
mkdir -p deploy/build/config
cp config/selectors.yaml deploy/build/config/selectors.yaml

TIPOS_YAML="$(printf '"%s", ' ${TIPOS//,/ })"
TIPOS_YAML="[${TIPOS_YAML%, }]"

cat > deploy/build/config/robots.yaml <<YAML
# Generado por scripts/publicar.sh: no editar a mano.
version: 1

portal:
  perfil_dir: "/opt/perfil"
  # En el contenedor se usa Xvfb: Chrome "con ventana" no se anuncia como
  # HeadlessChrome y el portal responde.
  headless: false
  download_dir: "/tmp/sri-downloads"

storage:
  ambiente: "${AMBIENTE}"

robots:
  - ruc: "${RUC}"
    password_env: "SRI_PASSWORD"
    etiqueta: "${AMBIENTE}"
    tipos: ${TIPOS_YAML}
    activo: true
YAML

rsync -a --delete "$PERFIL/" deploy/build/perfil/

# Los enlaces Singleton de macOS apuntan a la sesión del escritorio; Chrome en
# Linux los interpreta como "perfil en uso" y termina con código 21.
rm -f deploy/build/perfil/SingletonCookie deploy/build/perfil/SingletonLock deploy/build/perfil/SingletonSocket

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRO" >/dev/null

docker build -f deploy/Dockerfile -t "sri-txt-robot:base" .
docker build -f deploy/Dockerfile.lambda -t "$IMAGEN" .
docker push "$IMAGEN"

# -- Rol de la función -----------------------------------------------------
echo
echo "== Rol IAM: $ROL =="
if aws iam get-role --role-name "$ROL" >/dev/null 2>&1; then
    echo "Ya existe."
else
    aws iam create-role --role-name "$ROL" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
    aws iam attach-role-policy --role-name "$ROL" \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    echo "Creado."
fi

cat > deploy/build/politica-s3.json <<POLITICA
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
    "Resource": ["arn:aws:s3:::${BUCKET}", "arn:aws:s3:::${BUCKET}/*"]
  }]
}
POLITICA
aws iam put-role-policy --role-name "$ROL" --policy-name "AccesoBucketSri" \
    --policy-document file://deploy/build/politica-s3.json
ARN_ROL="arn:aws:iam::${CUENTA}:role/${ROL}"

# -- Función de Lambda -----------------------------------------------------
echo
echo "== Función Lambda: $FUNCION =="
# JSON generado con python3 para que la clave pueda llevar comas, '=' o comillas.
python3 - "$PASSWORD" "$AMBIENTE" "$BUCKET" "$ANTICAPTCHA" > deploy/build/entorno.json <<'PYJSON'
import json
import sys

variables = {
    "SRI_PASSWORD": sys.argv[1],
    "SRI_AMBIENTE": sys.argv[2],
    "SRI_BUCKET": sys.argv[3],
}
if sys.argv[4]:
    variables["ANTICAPTCHA_API_KEY"] = sys.argv[4]

json.dump({"Variables": variables}, sys.stdout)
PYJSON

if aws lambda get-function --function-name "$FUNCION" --region "$REGION" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FUNCION" --region "$REGION" \
        --image-uri "$IMAGEN" --output text >/dev/null
    aws lambda wait function-updated --function-name "$FUNCION" --region "$REGION"
    aws lambda update-function-configuration --function-name "$FUNCION" --region "$REGION" \
        --role "$ARN_ROL" --timeout "$TIMEOUT" --memory-size "$MEMORIA" \
        --environment file://deploy/build/entorno.json --output text >/dev/null
    aws lambda wait function-updated --function-name "$FUNCION" --region "$REGION"
    echo "Actualizada."
else
    # El rol tarda unos segundos en ser asumible por Lambda.
    for intento in 1 2 3 4 5; do
        if aws lambda create-function --function-name "$FUNCION" --region "$REGION" \
            --package-type Image --code "ImageUri=$IMAGEN" \
            --role "$ARN_ROL" --timeout "$TIMEOUT" --memory-size "$MEMORIA" \
            --architectures "$ARQUITECTURA" --ephemeral-storage "Size=2048" \
            --environment file://deploy/build/entorno.json --output text >/dev/null 2>&1; then
            break
        fi
        if [[ "$intento" -eq 5 ]]; then
            echo "No se pudo crear la función. Revise los permisos del rol $ROL." >&2
            exit 1
        fi
        echo "Reintentando la creación (el rol aún no propaga)..."
        sleep 8
    done
    aws lambda wait function-active --function-name "$FUNCION" --region "$REGION"
    echo "Creada."
fi

# -- Regla mensual de EventBridge -----------------------------------------
echo
echo "== Regla de EventBridge: $CRON =="
REGLA="${FUNCION}-mensual"
aws events put-rule --name "$REGLA" --region "$REGION" \
    --schedule-expression "$CRON" --state ENABLED >/dev/null
ARN_REGLA="$(aws events describe-rule --name "$REGLA" --region "$REGION" --query Arn --output text)"
aws lambda add-permission --function-name "$FUNCION" --region "$REGION" \
    --statement-id "${REGLA}-invocar" --action lambda:InvokeFunction \
    --principal events.amazonaws.com --source-arn "$ARN_REGLA" >/dev/null 2>&1 || true
aws events put-targets --rule "$REGLA" --region "$REGION" \
    --targets "Id=robot,Arn=${ARN_FUNCION},Input={}" >/dev/null
echo "Configurada."

echo
echo "Publicado."
echo "  Función:  $FUNCION ($REGION, $ARQUITECTURA)"
echo "  Imagen:   $IMAGEN"
echo "  Bucket:   s3://$BUCKET/$AMBIENTE/$RUC/<periodo>/"
echo "  Prueba:   aws lambda invoke --function-name $FUNCION --region $REGION \\"
echo "              --payload '{\"periodo\":\"2026-09\"}' /tmp/salida.json && cat /tmp/salida.json"

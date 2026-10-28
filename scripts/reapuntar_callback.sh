#!/usr/bin/env bash
# Reapunta el callback firmado de la función Lambda a la URL pública de un
# túnel de desarrollo (Expose, ngrok, Cloudflare Tunnel…). El resto de
# variables de entorno de la función se conserva tal cual.
#
# Uso:
#   scripts/reapuntar_callback.sh https://abc123.sharedwithexpose.com
#   scripts/reapuntar_callback.sh --quitar
#
#   --funcion <nombre>   Función Lambda (por defecto sri-txt-robot).
#   --region <región>    Región de AWS (por defecto us-east-2).
#   --perfil <perfil>    Perfil de AWS CLI (por defecto, el del entorno).
#
# La URL del túnel cambia cada vez que se reinicia, así que este comando se
# ejecuta de nuevo cuando cambie. El robot no falla si el callback no llega:
# lo registra como advertencia.
set -euo pipefail

cd "$(dirname "$0")/.."

FUNCION="sri-txt-robot"
REGION="${AWS_REGION:-us-east-2}"
PERFIL="${AWS_PROFILE:-}"
URL=""

uso() {
    cat <<'TXT'
Uso: scripts/reapuntar_callback.sh <url-del-webhook> [opciones]
     scripts/reapuntar_callback.sh --quitar [opciones]

  <url-del-webhook>   URL completa del receptor, por ejemplo
                      https://abc.sharedwithexpose.com/api/v1/webhooks/sri-robot
  --quitar            Deja la función sin callback (conserva el secreto).
  --funcion <nombre>  Función Lambda (por defecto sri-txt-robot).
  --region <región>   Región de AWS (por defecto us-east-2).
  --perfil <perfil>   Perfil de AWS CLI (por defecto, el del entorno).
TXT
}

while [ $# -gt 0 ]; do
    case "$1" in
        --funcion)
            FUNCION="${2:?falta el valor de --funcion}"
            shift 2
            ;;
        --region)
            REGION="${2:?falta el valor de --region}"
            shift 2
            ;;
        --perfil)
            PERFIL="${2:?falta el valor de --perfil}"
            shift 2
            ;;
        --quitar)
            URL="--quitar"
            shift
            ;;
        -h|--help)
            uso
            exit 0
            ;;
        -*)
            echo "Opción desconocida: $1" >&2
            exit 2
            ;;
        *)
            URL="$1"
            shift
            ;;
    esac
done

if [[ -z "$URL" ]]; then
    echo "Falta la URL del túnel. Use --help para ver los ejemplos." >&2
    exit 2
fi

if [[ "$URL" != "--quitar" ]]; then
    URL="${URL%/}"
    if [[ ! "$URL" =~ ^https?:// ]]; then
        echo "La URL debe empezar por https:// (o http:// si es localhost)." >&2
        exit 2
    fi
fi

AWS=(aws --region "$REGION")
if [[ -n "$PERFIL" ]]; then
    AWS+=(--profile "$PERFIL")
fi

echo "== leyendo el entorno de $FUNCION =="
ACTUAL="$(mktemp)"
NUEVO="$(mktemp)"
trap 'rm -f "$ACTUAL" "$NUEVO"' EXIT

"${AWS[@]}" lambda get-function-configuration \
    --function-name "$FUNCION" \
    --query 'Environment.Variables' \
    --output json > "$ACTUAL"

python3 - "$ACTUAL" "$NUEVO" "$URL" <<'PY'
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

actual, destino, url = sys.argv[1], sys.argv[2], sys.argv[3]
variables = json.loads(Path(actual).read_text()) or {}

if url == "--quitar":
    variables.pop("SRI_CALLBACK_URL", None)
    variables.pop("SRI_CALLBACK_ALLOWED_HOSTS", None)
    print("callback desactivado")
else:
    host = urlparse(url).hostname or ""
    variables["SRI_CALLBACK_URL"] = url
    variables["SRI_CALLBACK_ALLOWED_HOSTS"] = host
    print(f"SRI_CALLBACK_URL={url}")
    print(f"SRI_CALLBACK_ALLOWED_HOSTS={host}")

Path(destino).write_text(json.dumps({"Variables": variables}))
PY

if ! grep -q '"SRI_CALLBACK_SECRET"' "$NUEVO"; then
    echo "Aviso: la función no tiene SRI_CALLBACK_SECRET; el callback no se enviará." >&2
fi

echo "== actualizando $FUNCION =="
"${AWS[@]}" lambda update-function-configuration \
    --function-name "$FUNCION" \
    --environment "file://$NUEVO" \
    --output text >/dev/null
"${AWS[@]}" lambda wait function-updated --function-name "$FUNCION"

echo "listo: $FUNCION apunta a ${URL/--quitar/(sin callback)}"

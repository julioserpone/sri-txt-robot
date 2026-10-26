#!/usr/bin/env bash
# Construye localmente los artefactos del despliegue ZIP:
#
#   dist/runtime/browser-<arch>-<playwright>-<chromium>.tar.gz
#   dist/runtime/perfil.tar.gz
#   dist/funcion.zip
#   dist/version.json
#
# No toca AWS. Para publicar, use scripts/publicar_zip.sh.
#
# Uso:
#   scripts/construir_zip.sh [--ambiente produccion]
set -euo pipefail

cd "$(dirname "$0")/.."

AMBIENTE="produccion"
while [ $# -gt 0 ]; do
    case "$1" in
        --ambiente)
            AMBIENTE="${2:?falta el valor de --ambiente}"
            shift 2
            ;;
        -h|--help)
            sed -n '1,14p' "$0"
            exit 0
            ;;
        *)
            echo "Opción desconocida: $1" >&2
            exit 2
            ;;
    esac
done

mkdir -p dist/runtime

echo "== imagen constructora =="
docker build --platform linux/arm64 -f deploy/Dockerfile.artefactos -t sri-txt-robot:artefactos .

echo "== bundle y ZIP =="
docker run --rm --platform linux/arm64 \
    -e AMBIENTE="$AMBIENTE" \
    -e SALIDA=/salida \
    -v "$PWD":/proyecto:ro \
    -v "$PWD/dist":/salida \
    sri-txt-robot:artefactos

echo "== perfil de Chrome =="
if [ -d deploy/build/perfil ]; then
    tar -czf dist/runtime/perfil.tar.gz -C deploy/build/perfil .
    shasum -a 256 dist/runtime/perfil.tar.gz | awk '{print $1}' > dist/runtime/perfil.tar.gz.sha256
    ls -lh dist/runtime/perfil.tar.gz
else
    echo "AVISO: no existe deploy/build/perfil; el perfil no se incluyó." >&2
fi

echo "== artefactos =="
ls -lh dist/runtime dist/funcion.zip dist/version.json

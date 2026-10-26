#!/usr/bin/env bash
# Empaqueta el bundle del navegador y el ZIP de la función.
#
# Se ejecuta dentro de la imagen deploy/Dockerfile.artefactos, que es la misma
# base AL2023 del runtime de Lambda. El repositorio se monta en /proyecto y la
# salida en /salida.
#
# Salidas:
#   runtime/browser-<arch>-<playwright>-<chromium>.tar.gz  (+ .sha256)
#   funcion.zip                                           (código + deps)
#   version.json                                          (metadatos)
set -euo pipefail

SALIDA="${SALIDA:-/salida}"
PROYECTO="${PROYECTO:-/proyecto}"
AMBIENTE="${AMBIENTE:-produccion}"
RAIZ=/tmp/sri-runtime
PAQUETE=/tmp/paquete

PW_VERSION="$(python -c 'from importlib.metadata import version; print(version("playwright"))')"
CHROME_DIR="$(ls -d /opt/playwright/chromium-* | head -1)"
CHROME="$(ls -d "$CHROME_DIR"/chrome-linux*/chrome | head -1)"
REVISION="$(basename "$CHROME_DIR")"
ARCH="$(uname -m)"
NOMBRE="browser-${ARCH}-${PW_VERSION}-${REVISION}.tar.gz"

mkdir -p "$SALIDA/runtime"

echo "== bundle: Playwright $PW_VERSION, $REVISION, $ARCH =="
rm -rf "$RAIZ"
mkdir -p "$RAIZ/bin" "$RAIZ/lib" "$RAIZ/fonts" "$RAIZ/ms-playwright"

cp -a "$CHROME_DIR" "$RAIZ/ms-playwright/"
cp /usr/bin/Xvfb "$RAIZ/bin/Xvfb"
cp /usr/bin/xkbcomp "$RAIZ/bin/xkbcomp"
cp -a /usr/share/fonts/. "$RAIZ/fonts/"
# Xvfb necesita la base de teclados de xkeyboard-config; sin ella aborta con
# "Failed to compile keymap". No existe en el runtime de Lambda, así que viaja
# en el bundle y session.py la pasa con -xkbdir.
cp -a /usr/share/X11/xkb "$RAIZ/xkb"

# Xvfb compila el keymap ejecutando xkbcomp desde una ruta fijada al compilar
# ("/usr/bin"), y en Lambda /usr/bin es de solo lectura. Se redirige ese único
# literal a /tmp/xkb, donde runtime.py enlaza el xkbcomp del bundle. Si el
# binario cambia de formato, el build falla en vez de producir un bundle roto.
python - "$RAIZ/bin/Xvfb" <<'PY'
import sys

ruta = sys.argv[1]
datos = open(ruta, "rb").read()
if datos.count(b"/usr/bin\x00") != 1:
    raise SystemExit("No se pudo redirigir xkbcomp: el literal /usr/bin no es único.")
if b'"%s%sxkbcomp"' not in datos:
    raise SystemExit("No se encontró el comando de xkbcomp en Xvfb.")
open(ruta, "wb").write(datos.replace(b"/usr/bin\x00", b"/tmp/xkb\x00"))
PY

# Librerías que necesitan Chrome, Xvfb y xkbcomp. Se excluye el núcleo de glibc
# y el cargador dinámico: los aporta el runtime de Lambda (glibc 2.34) y
# mezclar versiones rompería el proceso.
ldd "$CHROME" /usr/bin/Xvfb /usr/bin/xkbcomp | awk '/=> \// {print $3}' | sort -u > /tmp/librerias.txt
while read -r libreria; do
    base="$(basename "$libreria")"
    case "$base" in
        libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|libresolv.so.*|libutil.so.*|libnsl.so.*|libanl.so.*|ld-linux*) continue ;;
    esac
    cp -L "$libreria" "$RAIZ/lib/" 2>/dev/null || true
done < /tmp/librerias.txt

# Chrome usa NSS para TLS y carga sus módulos PKCS#11 con dlopen, por lo que
# ldd no los ve. Sin ellos aborta con "FATAL:crypto/nss_util.cc nss_error=-5925"
# en el runtime de Lambda. Se copian junto a libnss3 para que se resuelvan por
# nombre desde LD_LIBRARY_PATH.
for modulo in libsoftokn3.so libfreebl3.so libfreeblpriv3.so libnssckbi.so \
              libnssdbm3.so libnsssysinit.so libnssutil3.so; do
    cp -L "/usr/lib64/$modulo" "$RAIZ/lib/" 2>/dev/null || true
done

cat > "$RAIZ/version.json" <<JSON
{
  "schema": 1,
  "playwright": "$PW_VERSION",
  "chromium": "$REVISION",
  "arch": "$ARCH",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

tar -czf "$SALIDA/runtime/$NOMBRE" -C "$RAIZ" .
( cd "$SALIDA/runtime" && sha256sum "$NOMBRE" > "$NOMBRE.sha256" )

echo "== ZIP de la función =="
rm -rf "$PAQUETE"
mkdir -p "$PAQUETE/config"
pip install --quiet --target "$PAQUETE" \
    "playwright==${PW_VERSION}" \
    "httpx>=0.27,<1" \
    "pydantic>=2.9,<3" \
    "PyNaCl>=1.5,<2" \
    "pyyaml>=6.0,<7"

cp -a "$PROYECTO/src/sri_txt_robot" "$PAQUETE/"
cp "$PROYECTO/config/selectors.yaml" "$PAQUETE/config/selectors.yaml"

# Configuración del modo dinámico: las credenciales llegan selladas en el
# evento y el perfil se materializa en /tmp.
cat > "$PAQUETE/config/robots.yaml" <<YAML
# Generado por scripts/construir_zip.sh: no editar a mano.
version: 1

portal:
  perfil_dir: "/tmp/perfil"
  headless: false
  download_dir: "/tmp/sri-downloads"

storage:
  ambiente: "$AMBIENTE"

robots: []
YAML

# __pycache__ y tests no viajan en el paquete.
find "$PAQUETE" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAQUETE" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true

( cd "$PAQUETE" && zip -q -r -X "$SALIDA/funcion.zip" . )

DESCOMPRIMIDO="$(du -sb "$PAQUETE" | awk '{print $1}')"
COMPRIMIDO="$(stat -c %s "$SALIDA/funcion.zip")"
BUNDLE="$(stat -c %s "$SALIDA/runtime/$NOMBRE")"

cat > "$SALIDA/version.json" <<JSON
{
  "schema": 1,
  "playwright": "$PW_VERSION",
  "chromium": "$REVISION",
  "arch": "$ARCH",
  "ambiente": "$AMBIENTE",
  "browser_object": "runtime/$NOMBRE",
  "browser_bytes": $BUNDLE,
  "funcion_zip_bytes": $COMPRIMIDO,
  "funcion_descomprimida_bytes": $DESCOMPRIMIDO
}
JSON

echo "== resultado =="
du -sh "$SALIDA/runtime/$NOMBRE" "$SALIDA/funcion.zip"
echo "ZIP descomprimido: $((DESCOMPRIMIDO / 1024 / 1024)) MB (límite Lambda: 250 MB)"

#!/bin/sh
# Arranca una pantalla virtual y entrega el control al runtime de Lambda.
#
# Chrome en modo headless se anuncia como "HeadlessChrome" y el portal del SRI
# deja de responder (la navegación se queda colgada hasta el tiempo límite).
# Con Xvfb el navegador se ejecuta "con ventana", igual que en macOS.
set -e

PANTALLA="${DISPLAY_NUM:-:99}"
NUMERO="${PANTALLA#:}"

# Chrome y fontconfig necesitan un HOME y un caché escribibles; en Lambda el
# usuario del runtime no puede escribir en /root.
export HOME=/tmp
export XDG_CACHE_HOME=/tmp/.cache
mkdir -p /tmp/.X11-unix /tmp/.cache
chmod 1777 /tmp/.X11-unix

# El perfil calentado viaja dentro de la imagen (/opt/perfil), pero /opt es de
# solo lectura en Lambda. La copia a /tmp la hace el handler al empezar: la
# fase de inicialización tiene un límite de 10 s y el perfil ronda los 40 MB.

Xvfb "$PANTALLA" -screen 0 "${DISPLAY_SIZE:-1440x900x24}" >/tmp/xvfb.log 2>&1 &

# No se entrega el control hasta que el socket de X exista: si Chrome arranca
# antes, termina con "Missing X server or $DISPLAY".
intentos=0
while [ ! -S "/tmp/.X11-unix/X$NUMERO" ]; do
    intentos=$((intentos + 1))
    if [ "$intentos" -ge 100 ]; then
        echo "Xvfb no arrancó en $PANTALLA:" >&2
        cat /tmp/xvfb.log >&2 2>/dev/null || true
        exit 1
    fi
    sleep 0.1
done

export DISPLAY="$PANTALLA"
echo "Xvfb escuchando en $PANTALLA (socket: $(ls /tmp/.X11-unix 2>/dev/null | tr '\n' ' '))" >&2

exec /usr/bin/python -m awslambdaric "$@"

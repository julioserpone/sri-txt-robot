#!/bin/sh
# Arranca una pantalla virtual y entrega el control al runtime de Lambda.
#
# Chrome en modo headless se anuncia como "HeadlessChrome" y el portal del SRI
# deja de responder (la navegación se queda colgada hasta el tiempo límite).
# Con Xvfb el navegador se ejecuta "con ventana", igual que en macOS.
set -e

Xvfb "${DISPLAY_NUM:-:99}" -screen 0 "${DISPLAY_SIZE:-1440x900x24}" >/tmp/xvfb.log 2>&1 &
export DISPLAY="${DISPLAY_NUM:-:99}"

exec /usr/bin/python -m awslambdaric "$@"

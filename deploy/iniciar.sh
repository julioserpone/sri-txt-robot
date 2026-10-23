#!/bin/sh
# Prepara el entorno y entrega el control al runtime de Lambda.
#
# Chrome en modo headless se anuncia como "HeadlessChrome" y el portal del SRI
# deja de responder (la navegación se queda colgada hasta el tiempo límite).
# Con Xvfb el navegador se ejecuta "con ventana", igual que en macOS.
#
# Xvfb se arranca de forma perezosa desde el handler, justo antes de abrir
# Chrome. Si se arranca aquí, el timeout de inicialización de Lambda (10 s)
# puede dejar el proceso muerto y un socket huérfano; el handler lo detecta y
# lo relanza.
set -e

# Chrome y fontconfig necesitan un HOME y un caché escribibles; en Lambda el
# usuario del runtime no puede escribir en /root.
export HOME=/tmp
export XDG_CACHE_HOME=/tmp/.cache
export DISPLAY="${DISPLAY_NUM:-:99}"
mkdir -p /tmp/.X11-unix /tmp/.cache
chmod 1777 /tmp/.X11-unix

# El perfil calentado viaja dentro de la imagen (/opt/perfil), pero /opt es de
# solo lectura en Lambda. La copia a /tmp la hace el handler al empezar: la
# fase de inicialización tiene un límite de 10 s y el perfil ronda los 40 MB.

exec /usr/bin/python -m awslambdaric "$@"

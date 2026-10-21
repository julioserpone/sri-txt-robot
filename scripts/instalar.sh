#!/usr/bin/env bash
# Crea el entorno virtual con un Python >= 3.12 y instala el robot.
#
# Uso:
#   bash scripts/instalar.sh
#
# Recrea .venv desde cero: un entorno reutilizado con otro intérprete deja un
# "pip" que corre sobre el Python equivocado y el resolver se queda minutos
# haciendo backtracking ("Using cached ...").
set -euo pipefail

cd "$(dirname "$0")/.."

MINIMA="3.12"
CANDIDATOS=(python3.13 python3.12 python3)

PYTHON=""
for candidato in "${CANDIDATOS[@]}"; do
    if command -v "$candidato" >/dev/null 2>&1 \
        && "$candidato" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        PYTHON="$candidato"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "No se encontró un Python >= ${MINIMA}." >&2
    echo "Instálelo con 'brew install python@3.13' y vuelva a ejecutar este script." >&2
    exit 1
fi

echo "Usando $PYTHON ($("$PYTHON" --version 2>&1))"

# --clear borra el contenido anterior: evita mezclar dos intérpretes.
"$PYTHON" -m venv --clear .venv

# Siempre "python -m pip": el script .venv/bin/pip puede apuntar a otro Python.
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

echo
echo "Listo. Active el entorno con:"
echo "    source .venv/bin/activate"

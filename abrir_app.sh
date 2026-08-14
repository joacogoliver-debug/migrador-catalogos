#!/usr/bin/env bash
# Abre el Migrador de Catálogos desde el código fuente (macOS / Linux).
# Para el ejecutable ya compilado no hace falta esto.
set -euo pipefail
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "No encontré Python 3. Instalalo desde https://www.python.org/downloads/"
  exit 1
fi

if ! "$PY" -c "import openpyxl" 2>/dev/null; then
  echo "Instalando dependencias por primera vez..."
  "$PY" -m pip install -r requirements-app.txt
fi

echo "Abriendo el Migrador de Catálogos..."
exec "$PY" app/launcher.py "$@"

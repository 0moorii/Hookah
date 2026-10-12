#!/bin/sh
# The public entrypoint keeps application code and its environment in .hookah.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
export HOOKAH_PACKAGE_ROOT="$ROOT" HOOKAH_PROGRAM_NAME="Hookah-Manager.sh"
if [ -n "${HOOKAH_PYTHON:-}" ]; then
    PYTHON=$HOOKAH_PYTHON
elif [ -x "$ROOT/.hookah/environment/bin/python" ]; then
    PYTHON="$ROOT/.hookah/environment/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
else
    printf '%s\n' 'Python 3.10+ is required. Install Python, then run Hookah-Manager.sh setup.' >&2
    exit 127
fi
exec "$PYTHON" -I -X utf8 -B "$ROOT/.hookah/launcher.py" "$@"

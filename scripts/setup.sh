#!/bin/sh
# One-time environment for the dm40 skill: a private venv with pyusb.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
if [ -x "$ROOT/.venv/bin/python" ] && "$ROOT/.venv/bin/python" -c 'import usb' 2>/dev/null; then
    echo "dm40: environment already present at $ROOT/.venv"
    exit 0
fi
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet pyusb
"$ROOT/.venv/bin/python" -c 'import usb; print("pyusb", usb.__version__)'
echo "dm40: ready ($ROOT/.venv)"

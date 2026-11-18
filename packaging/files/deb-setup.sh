#!/bin/sh
# .deb: builds the virtualenv /usr/lib/ccc/venv for the python3 installed now and installs the bundled ccc wheel
# with its dependencies from PyPI (needs network). Run by postinst.sh after ccc is installed or upgraded, and
# again (only when the virtualenv no longer works) after a python3 upgrade, by a dpkg trigger.
# Usage: deb-setup.sh [--if-broken]
set -e
lib=/usr/lib/ccc
venv=$lib/venv

check() { "$venv/bin/python" -c 'import ccc, ccc.cli, ccc.daemon, ccc.help, segno, textual, pyte' >/dev/null 2>&1; }

if [ "$1" = "--if-broken" ] && check; then
    exit 0
fi

wheel=$(ls "$lib"/wheels/ccc-*-py3-none-any.whl 2>/dev/null | head -n 1)
[ -n "$wheel" ] || { echo "ccc: no wheel in $lib/wheels" >&2; exit 1; }

rm -rf "$venv"
python3 -m venv "$venv"
if ! "$venv/bin/python" -m pip install --quiet --no-cache-dir --disable-pip-version-check "$wheel"; then
    rm -rf "$venv"
    echo "ccc: installing the Python dependencies failed (is the network up?)." >&2
    echo "ccc: fix it and run: sudo dpkg --configure ccc   (or: sudo $lib/deb-setup.sh)" >&2
    exit 1
fi
# Only `python -m ccc.…` is used (/usr/bin/ccc): drop the pip-made scripts and pip itself.
"$venv/bin/python" -m pip uninstall --quiet --yes pip >/dev/null 2>&1 || true
find "$venv/bin" -type f ! -name 'python*' -delete
check || { echo "ccc: the virtualenv does not import ccc" >&2; exit 1; }

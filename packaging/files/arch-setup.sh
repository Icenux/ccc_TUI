#!/bin/sh
# Arch package: builds the virtualenv /usr/lib/ccc/venv for the Python installed now. ccc and segno are copied from the
# package tree (/usr/lib/ccc/lib) into it; textual and pyte come from the system (--system-site-packages).
# Run by the pacman hook (packaging/files/ccc-venv.hook) after ccc is installed or upgraded and after a Python upgrade.
set -e
lib=/usr/lib/ccc
venv=$lib/venv
rm -rf "$venv"
python3 -m venv --system-site-packages --without-pip "$venv"
site=$("$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
cp -a "$lib/lib/." "$site/"
"$venv/bin/python" -m compileall -q "$site/ccc" "$site/segno" > /dev/null
"$venv/bin/python" -c 'import ccc, ccc.cli, ccc.daemon, ccc.help, segno, textual, pyte'

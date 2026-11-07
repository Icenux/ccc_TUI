#!/bin/sh
# Arch: the virtualenv is built on the machine (arch-setup.sh), so it is not part of the package.
if [ ! -e /usr/lib/ccc/arch-setup.sh ]; then
    rm -rf /usr/lib/ccc/venv
    rmdir /usr/lib/ccc 2>/dev/null || true
fi
exit 0

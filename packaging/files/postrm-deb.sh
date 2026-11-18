#!/bin/sh
# .deb: the virtualenv is built on the machine (deb-setup.sh), so dpkg does not know it; remove it with the package.
case "$1" in
    remove|purge)
        rm -rf /usr/lib/ccc/venv
        rmdir /usr/lib/ccc 2>/dev/null || true
        ;;
esac
exit 0

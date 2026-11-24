#!/usr/bin/env bash
# Builds the application tree the .deb installs into /usr/lib/ccc: the ccc wheel (pure Python) and the
# TUI supervisor script. The virtualenv is created at install time (packaging/files/deb-setup.sh), so one
# package runs on any python3 >= 3.12 (Ubuntu 24.04+, Debian 13+).
#
# Usage (from the repo root; needs python3 >= 3.12 with venv):  packaging/build.sh [VERSION]
# VERSION is stamped into the build (the release workflow passes the tag); without it the
# tree is a "0.0.0-dev" build. Result: dist/ccc/.
set -euo pipefail
umask 022
cd "$(dirname "$0")/.."

VERSION=${1:-}
app=dist/ccc
rm -rf dist/ccc build/src && mkdir -p "$app" build

# Build from a copy so the stamped version never touches the working tree.
cp -a src build/src && cp pyproject.toml build/ && cp README.md build/ 2>/dev/null || true
[ -z "$VERSION" ] || sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" build/src/ccc/__init__.py

# Only a wheel (pure Python) goes into the package: the virtualenv is built on the machine by
# packaging/files/deb-setup.sh (postinst), for whatever python3 it has, with the dependencies from PyPI.
mkdir -p "$app/wheels"
python3 -m pip wheel --quiet --no-cache-dir --no-deps --wheel-dir "$app/wheels" ./build
rm -rf build
case "$(ls "$app/wheels")" in
    ccc-*-py3-none-any.whl) ;;
    *) echo "expected one pure-Python ccc wheel, got: $(ls "$app/wheels")" >&2; exit 1 ;;
esac

cp bin/ccc-tui-supervise.sh "$app/ccc-tui-supervise.sh"
cp src/ccc/tmux.conf "$app/tmux.conf"  # ccc's own tmux configuration, read by the script

# Quick check that the wheel has the right version.
[ -z "$VERSION" ] || ls "$app/wheels" | grep -q "^ccc-${VERSION}-py3-none-any.whl$" \
    || { echo "built version is not $VERSION" >&2; exit 1; }
echo "✓ Built: $app ($(du -sh "$app" | cut -d' ' -f1))"

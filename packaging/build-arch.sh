#!/usr/bin/env bash
# Builds the application tree the Arch package installs into /usr/lib/ccc: ccc itself and the small pure-Python
# libraries Arch does not ship (segno) in dist/ccc-arch/lib/, plus the TUI supervisor script. Unlike the .deb there is no
# virtualenv in the package: Arch has a new Python every year, so /usr/lib/ccc/arch-setup.sh builds the virtualenv on the
# machine at install time (and again after a Python upgrade, by a pacman hook) from this tree and the system's
# python-textual / python-pyte.
#
# Usage (from the repo root; any python3 >= 3.12 with pip):  packaging/build-arch.sh [VERSION]
set -euo pipefail
umask 022
cd "$(dirname "$0")/.."

VERSION=${1:-}
out=dist/ccc-arch
rm -rf "$out" build/src && mkdir -p "$out/lib" build

# Build from a copy so the stamped version never touches the working tree.
cp -a src build/src && cp pyproject.toml build/ && cp README.md build/ 2>/dev/null || true
[ -z "$VERSION" ] || sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" build/src/ccc/__init__.py

# Only ccc and segno (no dependencies: textual and pyte come from the system packages).
python3 -m pip install --quiet --no-cache-dir --no-deps --target "$out/lib" ./build "segno>=1.6"
rm -rf build "$out/lib/bin"
find "$out/lib" -name __pycache__ -type d -prune -exec rm -rf {} +
# Pure Python only: a compiled module would be tied to one Python.
if find "$out/lib" \( -name '*.so' -o -name '*.pyd' \) | grep -q .; then
    echo "the Arch tree must be pure Python" >&2; exit 1
fi

cp bin/ccc-tui-supervise.sh "$out/ccc-tui-supervise.sh"

[ -z "$VERSION" ] || [ "$(PYTHONPATH="$out/lib" python3 -c 'import ccc; print(ccc.__version__)')" = "$VERSION" ] \
    || { echo "built version is not $VERSION" >&2; exit 1; }
echo "✓ Built: $out ($(du -sh "$out" | cut -d' ' -f1))"

#!/usr/bin/env bash
# From the built tree (dist/ccc, see packaging/build.sh) makes the release files in dist/release/:
# ccc_all.deb, the Arch package ccc-VERSION-1-any.pkg.tar.zst and SHA256SUMS (signed later, locally: ccc-deb-sign-upload.sh).
#
# Usage: packaging/package.sh VERSION     (needs nfpm in PATH; run packaging/build.sh and packaging/build-arch.sh first)
set -euo pipefail
umask 022
cd "$(dirname "$0")/.."

VERSION=${1:?usage: packaging/package.sh VERSION}
export VERSION
out=dist/release
[ -d dist/ccc-arch/lib/ccc ] || { echo "dist/ccc-arch is missing — run packaging/build-arch.sh first" >&2; exit 1; }
[ -x dist/ccc/venv/bin/python ] || { echo "dist/ccc is missing — run packaging/build.sh first" >&2; exit 1; }
built=$(dist/ccc/venv/bin/python -c 'import ccc; print(ccc.__version__)')
[ "$built" = "$VERSION" ] || { echo "Built version $built ≠ $VERSION" >&2; exit 1; }
rm -rf "$out" && mkdir -p "$out"

nfpm package --config packaging/nfpm.yaml --packager deb --target "$out/ccc_all.deb"
nfpm package --config packaging/nfpm.yaml --packager archlinux --target "$out/"
(cd "$out" && sha256sum ccc_all.deb ccc-*.pkg.tar.zst > SHA256SUMS)
ls -l "$out"

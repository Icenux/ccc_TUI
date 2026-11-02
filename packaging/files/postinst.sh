#!/bin/sh
# After installing and upgrading the package: sets up the Icenux package source (the shared APT
# repository of the Icenux applications) so later versions arrive with `apt upgrade`.
# The source files do not belong to the package (several Icenux applications would collide on one
# path): this script writes them, the list only when it is missing, the key always.
set -e
lib=/usr/lib/ccc

if [ -d /etc/apt/sources.list.d ] && [ -f "$lib/icenux.asc" ]; then
    install -d /usr/share/keyrings
    install -m 644 "$lib/icenux.asc" /usr/share/keyrings/icenux.asc
    [ -e /etc/apt/sources.list.d/icenux.list ] ||
        install -m 644 "$lib/icenux.list" /etc/apt/sources.list.d/icenux.list
    # A source written before the move to the Icenux organization still points at the old address.
    sed -i 's#luquas95\.github\.io/icenux/#icenux.github.io/icenux/#g' /etc/apt/sources.list.d/icenux.list
fi

cat <<'MSG'

ccc is installed. As your user (not root), once:
    systemctl --user enable --now ccc ccc-tui
    loginctl enable-linger "$USER"      # keep them running after you log out
then open it with: ccc
(After an upgrade: systemctl --user restart ccc ccc-tui)

MSG
exit 0

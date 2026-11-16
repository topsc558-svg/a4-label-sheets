#!/usr/bin/env bash
# Adds (or removes) an application-menu launcher for A4 Label Sheets in ~/.local/share/applications.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/etykiety-a4.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP"
    echo "Removed $DESKTOP"
    exit 0
fi

mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Etykiety A4
Comment=Print shipping labels onto A4 sticker sheets (4 per page)
Exec=python3 "$DIR/a4_label_sheets.py" %F
Icon=document-print
Terminal=false
Categories=Office;
MimeType=application/pdf;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" || true
echo "Installed launcher: $DESKTOP"

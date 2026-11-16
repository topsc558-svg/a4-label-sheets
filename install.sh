#!/usr/bin/env bash
# Adds (or removes) an application-menu launcher for A4 Label Sheets in ~/.local/share/applications.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS/a4-label-sheets.desktop"
LEGACY="$APPS/etykiety-a4.desktop"      # launcher from the earlier working name

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP" "$LEGACY"
    echo "Removed launcher(s) from $APPS"
    exit 0
fi

mkdir -p "$APPS"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=A4 Label Sheets
Name[pl]=Etykiety A4
Comment=Print shipping labels onto A4 sticker sheets (4 per page)
Comment[pl]=Drukuj etykiety wysyłkowe na arkuszach A4 z czterema naklejkami
Exec=python3 "$DIR/a4_label_sheets.py" %F
Icon=document-print
Terminal=false
Categories=Office;
MimeType=application/pdf;
EOF
rm -f "$LEGACY"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
echo "Installed launcher: $DESKTOP"

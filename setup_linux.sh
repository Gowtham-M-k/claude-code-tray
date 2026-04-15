#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Claude Code Tray — Linux setup
#
# Tested on: Ubuntu / Fedora / Arch  (GNOME, KDE, XFCE, i3)
# Installs deps + XDG autostart desktop entry
#
# Note: on GNOME 40+ you may need the AppIndicator extension:
#   https://extensions.gnome.org/extension/615/appindicator-support/
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/claude-tray.desktop"

echo "→ Installing Python dependencies..."
"$PYTHON" -m pip install -q --user -r "$SCRIPT_DIR/requirements.txt"

# pystray on Linux requires AppIndicator or Gtk backend
# Install system packages if not present
if command -v apt-get &>/dev/null; then
    echo "→ Installing GTK/AppIndicator system packages (sudo may prompt)..."
    sudo apt-get install -y -qq \
        python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        gir1.2-appindicator3-0.1 libayatana-appindicator3-1 \
        2>/dev/null || true
elif command -v dnf &>/dev/null; then
    sudo dnf install -y -q \
        python3-gobject gtk3 libappindicator-gtk3 \
        2>/dev/null || true
elif command -v pacman &>/dev/null; then
    sudo pacman -Sq --noconfirm python-gobject gtk3 libappindicator-gtk3 \
        2>/dev/null || true
fi

echo "→ Creating XDG autostart entry..."
mkdir -p "$AUTOSTART_DIR"
cat > "$DESKTOP_FILE" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Claude Code Tray
Comment=Status indicator for Claude Code
Exec=$PYTHON $SCRIPT_DIR/claude_tray.py
Icon=utilities-terminal
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
DESKTOP

chmod +x "$DESKTOP_FILE"

echo "✓ Done!  Autostart registered."
echo "  Starting tray now (you can close this terminal)..."
nohup "$PYTHON" "$SCRIPT_DIR/claude_tray.py" >> "$HOME/.claude-tray.log" 2>&1 &
echo "  PID $!  |  Logs → $HOME/.claude-tray.log"
echo ""
echo "  GNOME users: install the AppIndicator Shell Extension if the icon"
echo "  doesn't appear → https://extensions.gnome.org/extension/615/"

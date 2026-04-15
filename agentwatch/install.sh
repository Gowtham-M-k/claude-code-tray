#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AgentWatch — one-line macOS installer
#
#   curl -fsSL https://raw.githubusercontent.com/YOUR_USER/agentwatch/main/install.sh | sh
#
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/Gowtham-M-k/claude-code-tray/main/agentwatch"
INSTALL_DIR="$HOME/.agentwatch"
PLIST_PATH="$HOME/Library/LaunchAgents/com.agentwatch.plist"
LOG="$HOME/.agentwatch.log"
PYTHON="${PYTHON:-python3}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AgentWatch — Claude Code menu bar indicator"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python check ───────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "✗  python3 not found. Install from https://python.org and re-run."
    exit 1
fi
echo "→ Python: $("$PYTHON" --version)  ($("$PYTHON" -c 'import sys; print(sys.executable)'))"

# ── 2. Dependencies ───────────────────────────────────────────────────────────
echo ""
echo "→ Installing dependencies (rumps, psutil)..."
"$PYTHON" -m pip install --upgrade --quiet rumps psutil
echo "  ✓ done"

# ── 3. Download files ─────────────────────────────────────────────────────────
echo ""
echo "→ Downloading AgentWatch to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

curl -fsSL "$REPO_RAW/agentwatch_mac.py" -o "$INSTALL_DIR/agentwatch_mac.py"
curl -fsSL "$REPO_RAW/claude-color.svg"  -o "$INSTALL_DIR/claude-color.svg"
chmod +x "$INSTALL_DIR/agentwatch_mac.py"
echo "  ✓ done"

# ── 4. Stop any old instance ──────────────────────────────────────────────────
pkill -f "agentwatch_mac.py" 2>/dev/null || true

# ── 5. Smoke test ─────────────────────────────────────────────────────────────
echo ""
echo "→ Running smoke test (5 s)..."
"$PYTHON" "$INSTALL_DIR/agentwatch_mac.py" &
SMOKE_PID=$!
sleep 5
if kill -0 $SMOKE_PID 2>/dev/null; then
    echo "  ✓ Process is alive"
    kill $SMOKE_PID 2>/dev/null || true
else
    echo "  ✗ Process exited early — check output above"
    echo "     Try manually: python3 $INSTALL_DIR/agentwatch_mac.py"
    exit 1
fi

# ── 6. LaunchAgent (auto-start at login) ─────────────────────────────────────
echo ""
echo "→ Registering LaunchAgent → $PLIST_PATH"
mkdir -p "$HOME/Library/LaunchAgents"
PYBIN="$("$PYTHON" -c 'import sys; print(sys.executable)')"

# Create a launcher script named "agentwatch" so macOS shows that name
# instead of "python3" in Background Activity / Login Items.
LAUNCHER="$INSTALL_DIR/agentwatch"
cat > "$LAUNCHER" <<LAUNCHER_SCRIPT
#!/bin/bash
exec -a agentwatch "${PYBIN}" "${INSTALL_DIR}/agentwatch_mac.py" "\$@"
LAUNCHER_SCRIPT
chmod +x "$LAUNCHER"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agentwatch</string>
    <key>ProgramArguments</key>
    <array>
        <string>${LAUNCHER}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG}</string>
    <key>StandardErrorPath</key>
    <string>${LOG}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"
echo "  ✓ LaunchAgent loaded (starts automatically at login)"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓  AgentWatch is running!"
echo "     Look for the Claude icon in your menu bar (top-right)"
echo "     Logs → $LOG"
echo ""
echo "  To uninstall:"
echo "    launchctl unload $PLIST_PATH"
echo "    rm -rf $INSTALL_DIR $PLIST_PATH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

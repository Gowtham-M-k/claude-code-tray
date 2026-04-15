#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AgentWatch — macOS setup  (uses rumps, not pystray)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"
PLIST_PATH="$HOME/Library/LaunchAgents/com.agentwatch.plist"
INSTALL_DIR="$HOME/.agentwatch"
LOG="$HOME/.agentwatch.log"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " AgentWatch — macOS menu bar installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "→ Python: $("$PYTHON" --version)  at $("$PYTHON" -c 'import sys; print(sys.executable)')"

echo ""
echo "→ Installing dependencies (rumps + psutil)..."
"$PYTHON" -m pip install --upgrade rumps psutil

echo ""
echo "→ Installing script to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/agentwatch_mac.py" "$INSTALL_DIR/agentwatch_mac.py"

echo ""
echo "→ Killing any old AgentWatch instance..."
pkill -f "agentwatch_mac.py" 2>/dev/null || true

echo ""
echo "→ Test-launching AgentWatch (5 second smoke test)..."
"$PYTHON" "$INSTALL_DIR/agentwatch_mac.py" &
SMOKE_PID=$!
sleep 5
if kill -0 $SMOKE_PID 2>/dev/null; then
    echo "   ✓ Process is alive — looks good"
    kill $SMOKE_PID 2>/dev/null || true
else
    echo "   ✗ Process exited early — check above for errors"
    echo "   Try:  python3 agentwatch_mac.py"
    exit 1
fi

echo ""
echo "→ Writing LaunchAgent → $PLIST_PATH"
mkdir -p "$HOME/Library/LaunchAgents"
PYBIN="$("$PYTHON" -c 'import sys; print(sys.executable)')"
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
        <string>${PYBIN}</string>
        <string>${INSTALL_DIR}/agentwatch_mac.py</string>
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

echo "→ Loading LaunchAgent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✓  AgentWatch is running!"
echo "    Look for  ○ Claude  in your menu bar (top-right)"
echo "    Logs → $LOG"
echo ""
echo "    To uninstall:"
echo "    launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

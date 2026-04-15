#!/usr/bin/env python3
"""
Claude Code Status Tray
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cross-platform system tray indicator for Claude Code.
Platforms : macOS · Windows · Linux
Detection  : process tree monitoring (psutil)

States
  🟢 Working  — Claude Code is running AND has spawned child
                processes (executing a tool / shell command)
  🟡 Idle     — Claude Code is running, waiting for your input
  ⚫ Stopped  — Claude Code process not found
"""

import sys
import time
import threading
import platform
from pathlib import Path

# ── dependency check ──────────────────────────────────────────────────────────
_MISSING = []
try:
    from PIL import Image, ImageDraw
except ImportError:
    _MISSING.append("pillow")
try:
    import pystray
except ImportError:
    _MISSING.append("pystray")
try:
    import psutil
except ImportError:
    _MISSING.append("psutil")

if _MISSING:
    print(f"[claude-tray] Missing packages: {', '.join(_MISSING)}")
    print(f"  Run:  pip install {' '.join(_MISSING)}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration  (edit these if needed)
# ─────────────────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 2.0   # seconds between status polls
CPU_THRESHOLD   = 4.0   # % CPU → "working" when no children found
CPU_SAMPLE_TIME = 0.4   # seconds for cpu_percent measurement

# Strings that identify the Claude Code process
PROC_NAME_HINTS = ["claude"]
CMDLINE_HINTS   = [
    "claude",
    "@anthropic-ai/claude-code",
    "claude-code",
]

OS = platform.system()  # "Darwin" | "Windows" | "Linux"

# ─────────────────────────────────────────────────────────────────────────────
# Icon factory  — pure-pillow, no external assets needed
# ─────────────────────────────────────────────────────────────────────────────
_SZ = 64   # icon canvas size


def _make_icon(
    bg: tuple,
    ring: tuple | None = None,
    glyph: str | None = None,
) -> Image.Image:
    """Draw a circle icon.  bg/ring are RGBA tuples."""
    img = Image.new("RGBA", (_SZ, _SZ), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    m   = 4

    # outer ring
    if ring:
        d.ellipse([m, m, _SZ - m, _SZ - m], fill=ring)
        d.ellipse([m + 5, m + 5, _SZ - m - 5, _SZ - m - 5], fill=bg)
    else:
        d.ellipse([m, m, _SZ - m, _SZ - m], fill=bg)

    return img


# Pre-render the three states
_ICONS = {
    "working": _make_icon(
        bg=(34,  197, 94,  255),   # green-500
        ring=(22, 163, 74, 255),   # green-600 ring
    ),
    "idle": _make_icon(
        bg=(251, 191, 36, 255),    # amber-400
        ring=(217, 119, 6, 255),
    ),
    "stopped": _make_icon(
        bg=(107, 114, 128, 255),   # gray-500
    ),
}

_TOOLTIPS = {
    "working": "Claude Code  ⚙  Working",
    "idle":    "Claude Code  💤  Idle",
    "stopped": "Claude Code  ●  Not running",
}

_LABELS = {
    "working": "⚙️  Working (tool in progress)",
    "idle":    "💤  Idle — awaiting input",
    "stopped": "●  Not running",
}

# ─────────────────────────────────────────────────────────────────────────────
# Process detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_claude(proc: "psutil.Process") -> bool:
    """Return True if this process looks like Claude Code."""
    try:
        name = (proc.name() or "").lower()
        if any(h in name for h in PROC_NAME_HINTS):
            return True
        cmd = " ".join(proc.cmdline() or []).lower()
        return any(h in cmd for h in CMDLINE_HINTS)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def detect_status() -> str:
    """
    Returns one of: "working" | "idle" | "stopped"

    Strategy
    ────────
    1. Find all processes that look like Claude Code.
    2. If none → "stopped".
    3. If any has live child processes → "working"
       (Claude Code spawns shells / interpreters when executing tools).
    4. If CPU usage is elevated → "working".
    5. Otherwise → "idle".
    """
    candidates: list["psutil.Process"] = []
    for proc in psutil.process_iter():
        if _is_claude(proc):
            candidates.append(proc)

    if not candidates:
        return "stopped"

    for proc in candidates:
        try:
            # Child processes = tool execution in progress
            if proc.children(recursive=True):
                return "working"
            # Fallback: high CPU (streaming / local processing)
            cpu = proc.cpu_percent(interval=CPU_SAMPLE_TIME)
            if cpu >= CPU_THRESHOLD:
                return "working"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return "idle"


# ─────────────────────────────────────────────────────────────────────────────
# Tray application
# ─────────────────────────────────────────────────────────────────────────────

class ClaudeCodeTray:

    def __init__(self):
        self._stop   = threading.Event()
        self._status = ""          # force first update

        self.tray = pystray.Icon(
            name  = "claude_code_status",
            icon  = _ICONS["stopped"],
            title = _TOOLTIPS["stopped"],
            menu  = self._build_menu("stopped"),
        )

    # ── menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self, status: str) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(_LABELS[status], None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open Claude Code docs",
                lambda: self._open_url("https://docs.anthropic.com/en/docs/claude-code/overview"),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _open_url(url: str):
        import webbrowser
        webbrowser.open(url)

    def _apply(self, status: str):
        if status == self._status:
            return
        self._status     = status
        self.tray.icon   = _ICONS[status]
        self.tray.title  = _TOOLTIPS[status]
        self.tray.menu   = self._build_menu(status)
        # pystray needs an explicit notify on some Linux WMs
        try:
            self.tray.update_menu()
        except Exception:
            pass

    # ── poll loop ─────────────────────────────────────────────────────────────

    def _poll(self):
        while not self._stop.is_set():
            try:
                self._apply(detect_status())
            except Exception as exc:
                print(f"[claude-tray] poll error: {exc}", file=sys.stderr)
            self._stop.wait(POLL_INTERVAL)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _quit(self, *_):
        self._stop.set()
        self.tray.stop()

    def run(self):
        t = threading.Thread(target=self._poll, daemon=True)
        t.start()
        print(f"[claude-tray] Running on {OS}.  Right-click tray icon → Quit to exit.")
        self.tray.run()


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ClaudeCodeTray().run()

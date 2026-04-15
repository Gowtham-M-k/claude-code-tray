#!/usr/bin/env python3
"""
AgentWatch — macOS menu bar indicator for Claude Code
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses `rumps` — the native macOS menu bar library.
No pyobjc-core, no pystray, no broken deps.

States shown in the menu bar:
  ⚙  Working  — Claude Code is executing a tool / shell command
  ●  Idle     — Claude Code is running, waiting for input
  ○  Stopped  — Claude Code process not found
"""

import sys
import threading

# Hide from Dock and App Switcher — must happen before rumps/AppKit initialises
try:
    import AppKit
    AppKit.NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
except Exception:
    pass

# ── dependency check ──────────────────────────────────────────────────────────
_MISSING = []
try:
    import rumps
except ImportError:
    _MISSING.append("rumps")
try:
    import psutil
except ImportError:
    _MISSING.append("psutil")

if _MISSING:
    print(f"[AgentWatch] Missing: pip install {' '.join(_MISSING)}")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
POLL_INTERVAL   = 1.0   # seconds
CPU_THRESHOLD   = 4.0   # % CPU that counts as "working"
CPU_SAMPLE_TIME = 0.1   # seconds to sample CPU

PROC_NAME_HINTS = ["claude"]
CMDLINE_HINTS   = ["claude", "@anthropic-ai/claude-code", "claude-code"]

# Menu bar text for each state
STATE_TITLE = {
    "working": "🟢 Claude",
    "idle":    "🟡 Claude",
    "stopped": "🔴 Claude",
}

STATE_LABEL = {
    "working": "🟢  Working",
    "idle":    "🟡  Idle — waiting for input",
    "stopped": "🔴  Not running",
}

# ─────────────────────────────────────────────────────────────────────────────
# Process detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_claude(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
        if any(h in name for h in PROC_NAME_HINTS):
            return True
        cmd = " ".join(proc.cmdline() or []).lower()
        return any(h in cmd for h in CMDLINE_HINTS)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def detect_status() -> str:
    candidates = [p for p in psutil.process_iter() if _is_claude(p)]
    if not candidates:
        return "stopped"
    for proc in candidates:
        try:
            if proc.children(recursive=True):
                return "working"
            if proc.cpu_percent(interval=CPU_SAMPLE_TIME) >= CPU_THRESHOLD:
                return "working"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return "idle"


# ─────────────────────────────────────────────────────────────────────────────
# rumps app
# ─────────────────────────────────────────────────────────────────────────────

class AgentWatch(rumps.App):

    def __init__(self):
        super().__init__(
            name  = "AgentWatch",
            title = STATE_TITLE["stopped"],   # menu bar text
            quit_button = None,               # we add our own
        )
        self._status = ""

        # Status label (top of menu, non-clickable)
        self._status_item = rumps.MenuItem(STATE_LABEL["stopped"])
        self._status_item.set_callback(None)  # non-clickable

        self.menu = [
            self._status_item,
            None,                              # separator
            rumps.MenuItem("Open Claude Code docs", callback=self._open_docs),
            None,
            rumps.MenuItem("Quit AgentWatch",  callback=rumps.quit_application),
        ]

        # Start poll thread
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    # ── polling ───────────────────────────────────────────────────────────────

    def _poll_loop(self):
        import time
        while True:
            try:
                self._apply(detect_status())
            except Exception as e:
                print(f"[AgentWatch] poll error: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)

    @rumps.timer(1)   # belt-and-suspenders: rumps timer fires on main thread
    def _timer_tick(self, _sender):
        pass  # actual work done in thread; this keeps runloop alive

    # ── update UI ────────────────────────────────────────────────────────────

    def _apply(self, status: str):
        if status == self._status:
            return
        self._status = status
        # rumps UI updates must happen on main thread
        rumps.App.title.fget  # touch to confirm attribute exists
        self.title = STATE_TITLE[status]
        self._status_item.title = STATE_LABEL[status]

    # ── menu actions ─────────────────────────────────────────────────────────

    def _open_docs(self, _sender):
        import webbrowser
        webbrowser.open("https://docs.anthropic.com/en/docs/claude-code/overview")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    AgentWatch().run()

#!/usr/bin/env python3
"""
AgentWatch — macOS menu bar indicator for Claude Code
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses `rumps` — the native macOS menu bar library.

States shown in the menu bar icon:
  🟢 spinning arc  — Working (tool in progress)
  🟡 solid circle  — Idle (waiting for input)
  🔴 solid circle  — Stopped (not running)
"""

import sys
import os
import threading
import math

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
POLL_INTERVAL    = 1.0    # seconds between status checks
CPU_THRESHOLD    = 4.0    # % CPU that counts as "working"
CPU_SAMPLE_TIME  = 0.1    # seconds to sample CPU
ANIM_INTERVAL    = 0.12   # seconds between animation frames
DEBOUNCE_COUNT   = 3      # polls a status must be stable before switching

# Path to the Claude SVG logo (same directory as this script)
SVG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-color.svg")

# Colors (R, G, B) 0–1 range
COLOR_WORKING = (0.20, 0.78, 0.35)   # green
COLOR_IDLE    = (0.95, 0.75, 0.10)   # yellow
COLOR_STOPPED = (0.90, 0.25, 0.25)   # red

STATE_LABEL = {
    "working": "🟢  Working",
    "idle":    "🟡  Idle — waiting for input",
    "stopped": "🔴  Not running",
}

# ─────────────────────────────────────────────────────────────────────────────
# SVG logo — loaded once at startup, reused for every frame
# ─────────────────────────────────────────────────────────────────────────────

def _load_svg_logo(size: float):
    """Load claude-color.svg as an NSImage scaled to `size` points."""
    import AppKit
    img = AppKit.NSImage.alloc().initWithContentsOfFile_(SVG_PATH)
    if img is None:
        return None
    img.setSize_((size, size))
    return img

# Pre-load at module level (logo_size = 22 * 0.75 = 16.5pt)
_LOGO_SIZE   = 22.0 * 0.75
_CLAUDE_LOGO = None   # initialised on first use (AppKit not ready at import time)


def _get_logo():
    global _CLAUDE_LOGO
    if _CLAUDE_LOGO is None:
        _CLAUDE_LOGO = _load_svg_logo(_LOGO_SIZE)
    return _CLAUDE_LOGO


# ─────────────────────────────────────────────────────────────────────────────
# Icon rendering
# ─────────────────────────────────────────────────────────────────────────────

def _make_icon(state: str, frame: int = 0):
    """Render a 22×22 menu bar NSImage: Claude SVG logo + status dot badge."""
    import AppKit, Quartz

    size = 22.0
    canvas = AppKit.NSImage.alloc().initWithSize_((size, size))
    canvas.lockFocus()

    ctx = AppKit.NSGraphicsContext.currentContext().CGContext()

    # ── Claude logo ──────────────────────────────────────────────────────────
    logo = _get_logo()
    logo_size = _LOGO_SIZE
    logo_x = (size - logo_size) / 2
    logo_y = (size - logo_size) / 2 + size * 0.04   # slight upward nudge

    if logo is not None:
        logo.drawAtPoint_fromRect_operation_fraction_(
            (logo_x, logo_y),
            ((0, 0), (logo_size, logo_size)),
            AppKit.NSCompositeSourceOver,
            1.0,
        )
    else:
        # Fallback: plain coral circle if SVG missing
        Quartz.CGContextSetRGBFillColor(ctx, 0.85, 0.47, 0.34, 1.0)
        Quartz.CGContextAddArc(ctx, size / 2, size / 2, size * 0.36, 0, 2 * math.pi, 0)
        Quartz.CGContextFillPath(ctx)

    # ── Status dot (bottom-right corner) ────────────────────────────────────
    cr, cg, cb = {
        "working": COLOR_WORKING,
        "idle":    COLOR_IDLE,
        "stopped": COLOR_STOPPED,
    }[state]

    dot_r  = size * 0.155
    # Inset enough so the arc + stroke don't clip at canvas edge
    arc_stroke = 1.4
    track_extra = 1.8
    margin = dot_r + track_extra + arc_stroke / 2 + 1.0
    dot_cx = size - margin
    dot_cy = margin

    if state == "working":
        # Spinning arc around the dot
        track_r = dot_r + track_extra
        Quartz.CGContextSetRGBStrokeColor(ctx, cr, cg, cb, 0.25)
        Quartz.CGContextSetLineWidth(ctx, arc_stroke)
        Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapRound)
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, 0, 2 * math.pi, 0)
        Quartz.CGContextStrokePath(ctx)

        start_angle = (frame * 30) * math.pi / 180
        end_angle   = start_angle + 1.5 * math.pi
        Quartz.CGContextSetRGBStrokeColor(ctx, cr, cg, cb, 1.0)
        Quartz.CGContextSetLineWidth(ctx, arc_stroke)
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, start_angle, end_angle, 0)
        Quartz.CGContextStrokePath(ctx)

    # White border ring + colored fill
    Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r + 1.0, 0, 2 * math.pi, 0)
    Quartz.CGContextFillPath(ctx)

    Quartz.CGContextSetRGBFillColor(ctx, cr, cg, cb, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r, 0, 2 * math.pi, 0)
    Quartz.CGContextFillPath(ctx)

    canvas.unlockFocus()
    canvas.setTemplate_(False)
    return canvas


# ─────────────────────────────────────────────────────────────────────────────
# Process detection
# ─────────────────────────────────────────────────────────────────────────────

PROC_NAME_HINTS = ["claude"]
CMDLINE_HINTS   = ["claude", "@anthropic-ai/claude-code", "claude-code"]


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
            name        = "AgentWatch",
            title       = "",        # empty title — icon only
            quit_button = None,
        )
        self._status        = "stopped"
        self._pending       = "stopped"   # candidate status before debounce
        self._pending_count = 0           # consecutive polls with same candidate
        self._anim_frame    = 0
        self._lock          = threading.Lock()
        # Pre-render initial icon; applied on first _anim_tick
        # (_nsapp not available until run(), so can't call _set_icon here)
        self._icon_nsimage = _make_icon("stopped")

        self._status_item = rumps.MenuItem(STATE_LABEL["stopped"])
        self._status_item.set_callback(None)

        self.menu = [
            self._status_item,
            None,
            rumps.MenuItem("Open Claude Code docs", callback=self._open_docs),
            None,
            rumps.MenuItem("Quit AgentWatch", callback=rumps.quit_application),
        ]

        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    # ── icon helper ──────────────────────────────────────────────────────────

    def _set_icon(self, nsimage):
        """Push an NSImage to the menu bar status item via rumps internals."""
        self._icon_nsimage = nsimage
        self._nsapp.setStatusBarIcon()

    # ── polling ───────────────────────────────────────────────────────────────

    def _poll_loop(self):
        import time
        while True:
            try:
                raw = detect_status()
                with self._lock:
                    if raw == self._pending:
                        self._pending_count += 1
                    else:
                        self._pending       = raw
                        self._pending_count = 1
                    # Only commit when stable for DEBOUNCE_COUNT polls
                    if self._pending_count >= DEBOUNCE_COUNT:
                        self._status = self._pending
            except Exception as e:
                print(f"[AgentWatch] poll error: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)

    # ── animation timer (runs on main thread) ────────────────────────────────

    @rumps.timer(ANIM_INTERVAL)
    def _anim_tick(self, _sender):
        with self._lock:
            status = self._status

        # Always keep menu label in sync
        self._status_item.title = STATE_LABEL[status]

        if status == "working":
            self._anim_frame = (self._anim_frame + 1) % 12
            self._set_icon(_make_icon("working", self._anim_frame))
            self._last_static = None   # reset static-draw cache
        else:
            # Only redraw static icon when state actually changes
            if getattr(self, "_last_static", None) != status:
                self._last_static = status
                self._anim_frame  = 0
                self._set_icon(_make_icon(status))

    # ── menu actions ─────────────────────────────────────────────────────────

    def _open_docs(self, _sender):
        import webbrowser
        webbrowser.open("https://docs.anthropic.com/en/docs/claude-code/overview")


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    AgentWatch().run()

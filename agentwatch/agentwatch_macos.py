"""
AgentWatch macOS UI — two-panel NSPanel popover
Left panel : metric cards with sparklines
Right panel: metric detail with large chart, stats grid, recent actions
"""
import math
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import rumps

from agentwatch_alerts import AlertManager
from agentwatch_core import (
    ANIM_INTERVAL,
    CONFIG_PATH,
    DEBOUNCE_COUNT,
    JSONL_GLOB,
    METRICS_INTERVAL,
    NO_DATA_LABEL,
    POLL_INTERVAL,
    REPO_RAW,
    STATE_LABEL,
    VERSION_FILENAME,
    WORKING_HOLD_SEC,
    detect_process_state,
    format_cache_rate,
    format_compact,
    format_usd,
    get_version,
    load_config,
    make_summary,
    scan_metrics,
)
from agentwatch_updater import apply_update, check_remote_version

SVG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-color.svg")

# ── Colours ───────────────────────────────────────────────────────────────────
COLOR_WORKING = (0.20, 0.78, 0.35)
COLOR_IDLE    = (0.95, 0.75, 0.10)
COLOR_STOPPED = (0.90, 0.25, 0.25)

BG_PANEL     = (0.10, 0.10, 0.12, 1.0)
BG_CARD      = (0.14, 0.14, 0.17, 1.0)
BG_CARD_SEL  = (0.18, 0.18, 0.22, 1.0)
BG_RIGHT     = (0.08, 0.08, 0.10, 1.0)
BG_DETAIL    = (0.13, 0.13, 0.16, 1.0)
ACCENT_GREEN = (0.20, 0.78, 0.35, 1.0)
ACCENT_AMBER = (0.95, 0.60, 0.10, 1.0)
ACCENT_BLUE  = (0.30, 0.55, 1.00, 1.0)
ACCENT_TEAL  = (0.20, 0.75, 0.70, 1.0)
TEXT_PRI     = (0.95, 0.95, 0.97, 1.0)
TEXT_SEC     = (0.50, 0.50, 0.55, 1.0)
TEXT_DIM     = (0.35, 0.35, 0.40, 1.0)

# Panel geometry
PANEL_W  = 680
PANEL_H  = 430
LEFT_W   = 240
RIGHT_W  = PANEL_W - LEFT_W
CARD_H   = 68
CARD_PAD = 8
SPARK_W  = 64
SPARK_H  = 28
CHART_H  = 90
HEADER_H = 44
TAB_H    = 36
FOOTER_H = 32

_LOGO_SIZE   = 22.0 * 0.75
_CLAUDE_LOGO = None


# ── SVG logo ──────────────────────────────────────────────────────────────────
def load_svg_logo(size: float):
    import AppKit
    img = AppKit.NSImage.alloc().initWithContentsOfFile_(SVG_PATH)
    if img is None:
        return None
    img.setSize_((size, size))
    return img


def get_logo():
    global _CLAUDE_LOGO
    if _CLAUDE_LOGO is None:
        _CLAUDE_LOGO = load_svg_logo(_LOGO_SIZE)
    return _CLAUDE_LOGO


# ── Tray icon ─────────────────────────────────────────────────────────────────
def make_icon(state: str, frame: int = 0):
    import AppKit, Quartz
    size = 22.0
    canvas = AppKit.NSImage.alloc().initWithSize_((size, size))
    canvas.lockFocus()
    ctx = AppKit.NSGraphicsContext.currentContext().CGContext()
    logo = get_logo()
    logo_x = (size - _LOGO_SIZE) / 2
    logo_y = (size - _LOGO_SIZE) / 2 + size * 0.04
    if logo is not None:
        logo.drawAtPoint_fromRect_operation_fraction_(
            (logo_x, logo_y), ((0, 0), (_LOGO_SIZE, _LOGO_SIZE)),
            AppKit.NSCompositeSourceOver, 1.0)
    else:
        Quartz.CGContextSetRGBFillColor(ctx, 0.85, 0.47, 0.34, 1.0)
        Quartz.CGContextAddArc(ctx, size/2, size/2, size*0.36, 0, 2*math.pi, 0)
        Quartz.CGContextFillPath(ctx)
    cr, cg, cb = {"working": COLOR_WORKING, "idle": COLOR_IDLE, "stopped": COLOR_STOPPED}[state]
    dot_r = size * 0.155
    arc_stroke = 1.4
    track_extra = 1.8
    margin = dot_r + track_extra + arc_stroke / 2 + 1.0
    dot_cx = size - margin
    dot_cy = margin
    if state == "working":
        track_r = dot_r + track_extra
        Quartz.CGContextSetRGBStrokeColor(ctx, cr, cg, cb, 0.25)
        Quartz.CGContextSetLineWidth(ctx, arc_stroke)
        Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapRound)
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, 0, 2*math.pi, 0)
        Quartz.CGContextStrokePath(ctx)
        start_angle = (frame * 30) * math.pi / 180
        end_angle = start_angle + 1.5 * math.pi
        Quartz.CGContextSetRGBStrokeColor(ctx, cr, cg, cb, 1.0)
        Quartz.CGContextSetLineWidth(ctx, arc_stroke)
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, start_angle, end_angle, 0)
        Quartz.CGContextStrokePath(ctx)
    Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r + 1.0, 0, 2*math.pi, 0)
    Quartz.CGContextFillPath(ctx)
    Quartz.CGContextSetRGBFillColor(ctx, cr, cg, cb, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r, 0, 2*math.pi, 0)
    Quartz.CGContextFillPath(ctx)
    canvas.unlockFocus()
    canvas.setTemplate_(False)
    return canvas


# ── Drawing helpers ───────────────────────────────────────────────────────────
def draw_sparkline(ctx, x, y, w, h, data, color, fill=True):
    import Quartz
    if not data or max(data) == 0:
        return
    mn, mx = 0, max(data)
    if mx == mn:
        mx = mn + 1
    n = len(data)
    px = lambda i: x + (i / (n - 1)) * w if n > 1 else x + w / 2
    py = lambda v: y + ((v - mn) / (mx - mn)) * h
    r, g, b, a = color
    Quartz.CGContextSetRGBStrokeColor(ctx, r, g, b, a)
    Quartz.CGContextSetLineWidth(ctx, 1.5)
    Quartz.CGContextSetLineCap(ctx, Quartz.kCGLineCapRound)
    Quartz.CGContextSetLineJoin(ctx, Quartz.kCGLineJoinRound)
    Quartz.CGContextBeginPath(ctx)
    Quartz.CGContextMoveToPoint(ctx, px(0), py(data[0]))
    for i in range(1, n):
        Quartz.CGContextAddLineToPoint(ctx, px(i), py(data[i]))
    Quartz.CGContextStrokePath(ctx)
    if fill:
        Quartz.CGContextBeginPath(ctx)
        Quartz.CGContextMoveToPoint(ctx, px(0), y)
        for i in range(n):
            Quartz.CGContextAddLineToPoint(ctx, px(i), py(data[i]))
        Quartz.CGContextAddLineToPoint(ctx, px(n - 1), y)
        Quartz.CGContextClosePath(ctx)
        Quartz.CGContextSetRGBFillColor(ctx, r, g, b, 0.15)
        Quartz.CGContextFillPath(ctx)


def _nscolor(r, g, b, a=1.0):
    import AppKit
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def _cgcolor(r, g, b, a=1.0):
    return _nscolor(r, g, b, a).CGColor()


def _nsfont(size, bold=False):
    import AppKit
    return AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size)


def _nsfont_mono(size):
    import AppKit
    return AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, 0.0)


def _set_btn_text_color(btn, title, font, color_tuple):
    """Set NSButton title with a specific text color via NSAttributedString."""
    import AppKit
    attrs = {
        AppKit.NSForegroundColorAttributeName: _nscolor(*color_tuple),
        AppKit.NSFontAttributeName: font,
    }
    attr_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs)
    btn.setAttributedTitle_(attr_str)


def _textfield(frame, text, font, color, bg=False, align=0, editable=False, lines=1):
    import AppKit
    tf = AppKit.NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setEditable_(editable)
    tf.setBordered_(False)
    tf.setDrawsBackground_(bg)
    tf.setTextColor_(color)
    tf.setFont_(font)
    tf.setAlignment_(align)
    if lines != 1:
        tf.setMaximumNumberOfLines_(lines)
    return tf


def _fmt_time(ts: str) -> str:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%-I:%M %p")
    except Exception:
        return ts[11:16] if len(ts) >= 16 else "—"


# ─────────────────────────────────────────────────────────────────────────────
# ObjC class: ChartView
# ─────────────────────────────────────────────────────────────────────────────
def _register_chart_view():
    import objc, AppKit

    class AWChartView(AppKit.NSView):
        @objc.python_method
        def initAW(self):
            self = objc.super(AWChartView, self).init()
            if self is None:
                return None
            self._aw_data = []
            self._aw_color = ACCENT_GREEN
            return self

        @objc.python_method
        def aw_set_data(self, data, color):
            self._aw_data = data
            self._aw_color = color
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            import AppKit, Quartz
            ctx = AppKit.NSGraphicsContext.currentContext().CGContext()
            w = self.bounds().size.width
            h = self.bounds().size.height
            data = getattr(self, "_aw_data", [])
            color = getattr(self, "_aw_color", ACCENT_GREEN)
            if data:
                draw_sparkline(ctx, 8, 8, w - 16, h - 16, data, color, fill=True)

    return AWChartView


_AWChartView = None


def get_chart_view_class():
    global _AWChartView
    if _AWChartView is None:
        _AWChartView = _register_chart_view()
    return _AWChartView


# ─────────────────────────────────────────────────────────────────────────────
# ObjC class: ButtonTarget (for footer button callbacks)
# ─────────────────────────────────────────────────────────────────────────────
def _register_button_target():
    import objc, AppKit

    class AWButtonTarget(AppKit.NSObject):
        @objc.python_method
        def initWithCallback_(self, cb):
            self = objc.super(AWButtonTarget, self).init()
            if self is None:
                return None
            self._cb = cb
            return self

        def buttonClicked_(self, sender):
            if callable(getattr(self, "_cb", None)):
                self._cb()

    return AWButtonTarget


_AWButtonTarget = None


def get_button_target_class():
    global _AWButtonTarget
    if _AWButtonTarget is None:
        _AWButtonTarget = _register_button_target()
    return _AWButtonTarget


# ─────────────────────────────────────────────────────────────────────────────
# MetricCard
# ─────────────────────────────────────────────────────────────────────────────
class MetricCard:
    def __init__(self, frame, key: str, label: str, color: tuple, on_click):
        import AppKit, objc
        self.key = key
        self.label = label
        self.color = color
        self._selected = False
        self._value_str = "—"
        self._subtitle = ""
        self._sparkdata = []
        self._on_click = on_click

        self.view = AppKit.NSView.alloc().initWithFrame_(frame)
        self.view.setWantsLayer_(True)
        self.view.layer().setCornerRadius_(8.0)
        self._refresh_bg()

        # Clickable button overlay (transparent, covers entire card)
        fw = frame[1][0]
        fh = frame[1][1]
        btn_target = get_button_target_class().alloc().initWithCallback_(self._clicked)
        self._btn_target = btn_target  # keep strong ref
        btn = AppKit.NSButton.alloc().initWithFrame_(((0, 0), (fw, fh)))
        btn.setTitle_("")
        btn.setBezelStyle_(0)
        btn.setButtonType_(0)
        btn.setBordered_(False)
        btn.setTransparent_(True)
        btn.setTarget_(btn_target)
        btn.setAction_(objc.selector(btn_target.buttonClicked_, selector=b"buttonClicked:", signature=b"v@:@"))
        self.view.addSubview_(btn)

        # Text subviews for label, value, subtitle
        r, g, b, a = color
        PAD = 10

        # Accent dot (via a tiny view)
        dot = AppKit.NSView.alloc().initWithFrame_(((PAD, fh - PAD - 8), (6, 6)))
        dot.setWantsLayer_(True)
        dot.layer().setBackgroundColor_(_cgcolor(r, g, b))
        dot.layer().setCornerRadius_(3.0)
        self.view.addSubview_(dot)

        # Label
        self._lbl_tf = _textfield(
            ((PAD + 12, fh - PAD - 14), (fw - PAD * 2 - 12, 14)),
            label, _nsfont(10), _nscolor(*TEXT_SEC)
        )
        self.view.addSubview_(self._lbl_tf)

        # Value
        self._val_tf = _textfield(
            ((PAD, fh // 2 - 12), (fw - SPARK_W - PAD * 2, 24)),
            "—", _nsfont_mono(18), _nscolor(*TEXT_PRI)
        )
        self.view.addSubview_(self._val_tf)

        # Subtitle
        self._sub_tf = _textfield(
            ((PAD, PAD), (fw - SPARK_W - PAD * 2, 14)),
            "", _nsfont(10), _nscolor(*TEXT_SEC)
        )
        self.view.addSubview_(self._sub_tf)

        # Sparkline canvas (drawn via a custom view)
        ChartClass = get_chart_view_class()
        spark_view = ChartClass.alloc().initWithFrame_(
            ((fw - SPARK_W - PAD, PAD), (SPARK_W, SPARK_H))
        )
        spark_view.initAW()
        self._spark_view = spark_view
        self.view.addSubview_(spark_view)

    def _clicked(self):
        self._on_click(self.key)

    def _refresh_bg(self):
        r, g, b, a = BG_CARD_SEL if self._selected else BG_CARD
        self.view.layer().setBackgroundColor_(_cgcolor(r, g, b, a))
        if self._selected:
            rc, gc, bc, ac = self.color
            self.view.layer().setBorderColor_(_cgcolor(rc, gc, bc, 0.8))
            self.view.layer().setBorderWidth_(2.0)
        else:
            self.view.layer().setBorderWidth_(0.0)

    def select(self, yes: bool):
        self._selected = yes
        self._refresh_bg()

    def update(self, value_str: str, subtitle: str, sparkdata: list):
        self._value_str = value_str
        self._subtitle = subtitle
        self._sparkdata = sparkdata
        self._val_tf.setStringValue_(value_str)
        self._sub_tf.setStringValue_(subtitle)
        self._spark_view.aw_set_data(sparkdata, self.color)


# ─────────────────────────────────────────────────────────────────────────────
# StatCell — small 2-line data cell
# ─────────────────────────────────────────────────────────────────────────────
class StatCell:
    def __init__(self, frame, label: str, value: str):
        import AppKit
        fw, fh = frame[1]
        self.container = AppKit.NSView.alloc().initWithFrame_(frame)
        self.container.setWantsLayer_(True)
        self.container.layer().setBackgroundColor_(_cgcolor(*BG_DETAIL))
        self.container.layer().setCornerRadius_(5.0)

        self._lbl = _textfield(
            ((6, fh // 2 + 1), (fw - 12, 13)),
            label, _nsfont(9), _nscolor(*TEXT_SEC)
        )
        self.container.addSubview_(self._lbl)

        self._val = _textfield(
            ((6, 3), (fw - 12, 16)),
            value, _nsfont(11, bold=True), _nscolor(*TEXT_PRI)
        )
        self.container.addSubview_(self._val)

    def update(self, label: str, value: str):
        self._lbl.setStringValue_(label)
        self._val.setStringValue_(value)


# ─────────────────────────────────────────────────────────────────────────────
# ActionRow — a single recent-action row
# ─────────────────────────────────────────────────────────────────────────────
TOOL_ICONS = {
    "read": "📄", "bash": "⌨", "write": "✏", "edit": "✏",
    "web": "🌐", "grep": "🔍", "glob": "📁", "search": "🔍",
}


class ActionRow:
    def __init__(self, frame):
        import AppKit
        fw, fh = frame[1]
        self.container = AppKit.NSView.alloc().initWithFrame_(frame)
        self.container.setWantsLayer_(True)
        self.container.layer().setBackgroundColor_(_cgcolor(*BG_DETAIL))
        self.container.layer().setCornerRadius_(4.0)

        self._icon = _textfield(((6, 3), (18, 16)), "📄", _nsfont(11), _nscolor(*TEXT_PRI))
        self.container.addSubview_(self._icon)

        self._name = _textfield(((28, 3), (fw - 80, 16)), "", _nsfont(11), _nscolor(*TEXT_PRI))
        self.container.addSubview_(self._name)

        self._time = _textfield(((fw - 52, 3), (48, 16)), "", _nsfont(10), _nscolor(*TEXT_DIM), align=1)
        self.container.addSubview_(self._time)

    def update(self, name: str, time_str: str):
        icon = "📄"
        nl = name.lower()
        for k, v in TOOL_ICONS.items():
            if k in nl:
                icon = v
                break
        self._icon.setStringValue_(icon)
        display = name.replace("_", " ").title() if name else ""
        self._name.setStringValue_(display)
        self._time.setStringValue_(time_str)
        self.container.setHidden_(not bool(name))


# ─────────────────────────────────────────────────────────────────────────────
# Left panel
# ─────────────────────────────────────────────────────────────────────────────
METRICS_USAGE = [
    ("tokens_in",  "Tokens In",      ACCENT_GREEN),
    ("tokens_out", "Tokens Out",      ACCENT_BLUE),
    ("agents",     "Active Agents",   ACCENT_GREEN),
    ("cost",       "Cost Today",      ACCENT_AMBER),
    ("cache",      "Cache Hit Rate",  ACCENT_TEAL),
]
METRICS_SYSTEM = [
    ("version",   "Version",         ACCENT_TEAL),
    ("sessions",  "Total Sessions",  ACCENT_GREEN),
    ("config",    "Config File",     ACCENT_TEAL),
]


class LeftPanel:
    def __init__(self, frame, on_select):
        import AppKit
        self._on_select = on_select
        self._tab = "usage"
        self._cards: dict = {}
        self._selected_key: Optional[str] = None

        self.view = AppKit.NSView.alloc().initWithFrame_(frame)
        self.view.setWantsLayer_(True)
        self.view.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))

        fw = frame[1][0]
        fh = frame[1][1]

        # ── Header ──
        hdr = AppKit.NSView.alloc().initWithFrame_(((0, fh - HEADER_H), (fw, HEADER_H)))
        hdr.setWantsLayer_(True)
        hdr.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))

        logo = get_logo()
        if logo:
            lv = AppKit.NSImageView.alloc().initWithFrame_(((8, 9), (24, 24)))
            big = load_svg_logo(24)
            if big:
                lv.setImage_(big)
            hdr.addSubview_(lv)

        title_tf = _textfield(
            ((38, 12), (fw - 110, 20)),
            "AgentWatch", _nsfont(13, bold=True), _nscolor(*TEXT_PRI)
        )
        hdr.addSubview_(title_tf)

        self._status_tf = _textfield(
            ((fw - 72, 14), (68, 16)),
            "● IDLE", _nsfont(10), _nscolor(*ACCENT_AMBER[:3]), align=1
        )
        hdr.addSubview_(self._status_tf)
        self.view.addSubview_(hdr)

        # ── Tab bar ──
        tab_y = fh - HEADER_H - TAB_H
        tab_bg = AppKit.NSView.alloc().initWithFrame_(((0, tab_y), (fw, TAB_H)))
        tab_bg.setWantsLayer_(True)
        tab_bg.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))

        btn_w = fw / 2
        self._tab_btns = {}
        for i, (tab_key, tab_label) in enumerate([("usage", "⚡ Usage"), ("system", "⚙ System")]):
            active = (tab_key == "usage")
            tgt = get_button_target_class().alloc().initWithCallback_(
                lambda k=tab_key: self._switch_tab(k)
            )
            self._tab_btn_targets = getattr(self, "_tab_btn_targets", [])
            self._tab_btn_targets.append(tgt)
            import objc
            btn = AppKit.NSButton.alloc().initWithFrame_(((i * btn_w, 2), (btn_w - 2, TAB_H - 4)))
            btn.setBezelStyle_(0)
            btn.setButtonType_(0)
            btn.setBordered_(False)
            btn.setFont_(_nsfont(11))
            btn.setWantsLayer_(True)
            btn.layer().setCornerRadius_(6.0)
            btn.setTarget_(tgt)
            btn.setAction_(objc.selector(tgt.buttonClicked_, selector=b"buttonClicked:", signature=b"v@:@"))
            if active:
                btn.layer().setBackgroundColor_(_cgcolor(*BG_CARD))
                _set_btn_text_color(btn, tab_label, _nsfont(11), TEXT_PRI)
            else:
                btn.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))
                _set_btn_text_color(btn, tab_label, _nsfont(11), TEXT_SEC)
            tab_bg.addSubview_(btn)
            self._tab_btns[tab_key] = btn

        self.view.addSubview_(tab_bg)
        self._tab_bar = tab_bg
        self._tab_y = tab_y

        # ── Cards ──
        self._cards_y_start = tab_y - CARD_PAD
        self._build_cards(fw)

    def _build_cards(self, fw):
        import AppKit
        # Remove old cards
        for key, card in self._cards.items():
            card.view.removeFromSuperview()
        self._cards = {}

        metrics = METRICS_USAGE if self._tab == "usage" else METRICS_SYSTEM
        for i, (key, label, color) in enumerate(metrics):
            cy = self._cards_y_start - (CARD_H + CARD_PAD) * (i + 1)
            card = MetricCard(
                ((CARD_PAD, cy), (fw - CARD_PAD * 2, CARD_H)),
                key, label, color, self._card_clicked
            )
            self.view.addSubview_(card.view)
            self._cards[key] = card

        # Selection deferred — call init_selection() after RightPanel exists
        self._deferred_first = metrics[0][0] if metrics else None

    def init_selection(self):
        """Call after RightPanel exists to select the initial card."""
        first = getattr(self, "_deferred_first", None)
        if first:
            self._select(first)

    def _switch_tab(self, tab_key: str):
        if tab_key == self._tab:
            return
        self._tab = tab_key
        import AppKit
        for k, btn in self._tab_btns.items():
            label = btn.attributedTitle().string() if btn.attributedTitle() else btn.title()
            if k == tab_key:
                btn.layer().setBackgroundColor_(_cgcolor(*BG_CARD))
                _set_btn_text_color(btn, label, _nsfont(11), TEXT_PRI)
            else:
                btn.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))
                _set_btn_text_color(btn, label, _nsfont(11), TEXT_SEC)
        fw = self.view.frame().size.width
        self._build_cards(fw)

    def _card_clicked(self, key: str):
        self._select(key)

    def _select(self, key: str):
        for k, card in self._cards.items():
            card.select(k == key)
        self._selected_key = key
        self._on_select(key)

    def set_status(self, status: str):
        lmap = {"working": ("● WORKING", ACCENT_GREEN), "idle": ("● IDLE", ACCENT_AMBER), "stopped": ("● STOPPED", (0.90, 0.25, 0.25, 1.0))}
        text, color = lmap.get(status, ("● IDLE", ACCENT_AMBER))
        self._status_tf.setStringValue_(text)
        self._status_tf.setTextColor_(_nscolor(*color[:3]))

    def update_cards(self, status: str, active_agents: int, m):
        self.set_status(status)
        cache_pct = format_cache_rate(m.cache_read, m.tokens_in)
        cost_pct = f"{round(m.cost_today / max(m.cost_all_time, 0.001) * 100)}% used" if m.cost_all_time else "0% used"
        data = {
            "tokens_in":  (format_compact(m.tokens_in_today),  f"all-time {format_compact(m.tokens_in)}",  m.tokens_in_history),
            "tokens_out": (format_compact(m.tokens_out_today), f"all-time {format_compact(m.tokens_out)}", m.tokens_out_history),
            "agents":     (str(active_agents), "Stable" if active_agents <= 2 else f"{active_agents} running", m.sessions_history),
            "cost":       (format_usd(m.cost_today), cost_pct, m.cost_history),
            "cache":      (cache_pct, "Prompt re-use rate", []),
            "version":    ("—", "Auto-update on", []),
            "sessions":   (str(m.sessions_total), f"▲ {m.sessions_today} today", m.sessions_history),
            "config":     ("Loaded", "~/.agentwatch.toml", []),
        }
        for key, card in self._cards.items():
            if key in data:
                v, sub, spark = data[key]
                card.update(v, sub, spark)


# ─────────────────────────────────────────────────────────────────────────────
# Right panel
# ─────────────────────────────────────────────────────────────────────────────
class RightPanel:
    def __init__(self, frame):
        import AppKit
        self.frame = frame
        self._metric_key = "tokens_in"
        self._status = "idle"
        self._active_agents = 0
        self._metrics = None
        self._version = "0.1.0"
        self._update_status = ""

        fw = frame[1][0]
        fh = frame[1][1]

        self.view = AppKit.NSView.alloc().initWithFrame_(frame)
        self.view.setWantsLayer_(True)
        self.view.layer().setBackgroundColor_(_cgcolor(*BG_RIGHT))

        # Section header
        self._hdr_tf = _textfield(
            ((16, fh - 28), (fw - 32, 18)),
            "METRIC DETAILS", _nsfont(9, bold=True), _nscolor(*TEXT_SEC)
        )
        self.view.addSubview_(self._hdr_tf)

        # Big value
        self._val_tf = _textfield(
            ((16, fh - 82), (fw - 110, 52)),
            "—", _nsfont_mono(36), _nscolor(*TEXT_PRI)
        )
        self.view.addSubview_(self._val_tf)

        # Badge
        self._badge = AppKit.NSTextField.alloc().initWithFrame_(((fw - 84, fh - 68), (76, 22)))
        self._badge.setStringValue_("Latest")
        self._badge.setEditable_(False)
        self._badge.setBordered_(False)
        self._badge.setDrawsBackground_(True)
        self._badge.setBackgroundColor_(_nscolor(*ACCENT_GREEN[:3], 0.2))
        self._badge.setTextColor_(_nscolor(*ACCENT_GREEN[:3]))
        self._badge.setFont_(_nsfont(10))
        self._badge.setAlignment_(1)
        self._badge.setWantsLayer_(True)
        self._badge.layer().setCornerRadius_(4.0)
        self.view.addSubview_(self._badge)

        # Chart bg + view
        chart_y = fh - 82 - CHART_H - 8
        chart_bg = AppKit.NSView.alloc().initWithFrame_(((12, chart_y), (fw - 24, CHART_H)))
        chart_bg.setWantsLayer_(True)
        chart_bg.layer().setBackgroundColor_(_cgcolor(*BG_DETAIL))
        chart_bg.layer().setCornerRadius_(8.0)
        self.view.addSubview_(chart_bg)

        ChartClass = get_chart_view_class()
        self._chart_view = ChartClass.alloc().initWithFrame_(((0, 0), (fw - 24, CHART_H)))
        self._chart_view.initAW()
        chart_bg.addSubview_(self._chart_view)

        # Description
        desc_y = chart_y - 38
        self._desc_tf = _textfield(
            ((16, desc_y), (fw - 32, 34)),
            "", _nsfont(11), _nscolor(*ACCENT_GREEN[:3]), lines=2
        )
        self.view.addSubview_(self._desc_tf)

        # Stats grid (2 cols × 2 rows)
        grid_y = desc_y - 74
        cell_w = (fw - 28) / 2
        cell_h = 32
        stat_labels = [("Avg / Day", "—"), ("Ratio", "—"), ("This Week", "—"), ("vs Yesterday", "—")]
        self._stat_cells = []
        for i, (lbl, val) in enumerate(stat_labels):
            col = i % 2
            row = i // 2
            cx = 12 + col * (cell_w + 4)
            cy = grid_y - row * (cell_h + 4)
            sc = StatCell(((cx, cy), (cell_w, cell_h)), lbl, val)
            self.view.addSubview_(sc.container)
            self._stat_cells.append(sc)

        # Recent actions header
        actions_y = grid_y - 84
        ra_hdr = _textfield(
            ((16, actions_y), (fw - 80, 18)),
            "RECENT ACTIONS", _nsfont(9, bold=True), _nscolor(*TEXT_SEC)
        )
        self.view.addSubview_(ra_hdr)

        live = _textfield(
            ((fw - 58, actions_y), (50, 16)),
            "● Live", _nsfont(10), _nscolor(*ACCENT_GREEN[:3])
        )
        self.view.addSubview_(live)

        # Action rows
        self._action_rows = []
        for i in range(4):
            ry = actions_y - 24 - i * 26
            row = ActionRow(((12, ry), (fw - 24, 22)))
            self.view.addSubview_(row.container)
            self._action_rows.append(row)

    def select_metric(self, key: str):
        self._metric_key = key
        self._refresh()

    def update(self, status, active_agents, metrics, version, update_status):
        self._status = status
        self._active_agents = active_agents
        self._metrics = metrics
        self._version = version
        self._update_status = update_status
        self._refresh()

    def _refresh(self):
        if self._metrics is None:
            return
        m = self._metrics
        key = self._metric_key
        cfg = self._cfg(key, m)

        self._hdr_tf.setStringValue_(cfg["header"])
        self._val_tf.setStringValue_(cfg["value"])
        self._desc_tf.setStringValue_(cfg["desc"])
        self._desc_tf.setTextColor_(_nscolor(*cfg["color"][:3]))
        self._badge.setStringValue_(cfg["badge"])
        bc = cfg.get("badge_color", ACCENT_GREEN)
        self._badge.setTextColor_(_nscolor(*bc[:3]))
        self._badge.setBackgroundColor_(_nscolor(*bc[:3], 0.18))

        self._chart_view.aw_set_data(cfg["chart_data"], cfg["color"])

        stats = cfg.get("stats", [("—", "—")] * 4)
        for i, sc in enumerate(self._stat_cells):
            lbl, val = stats[i] if i < len(stats) else ("—", "—")
            sc.update(lbl, val)

        actions = m.recent_actions[:4] if m.recent_actions else []
        for i, row in enumerate(self._action_rows):
            if i < len(actions):
                ts, name = actions[i]
                row.update(name, _fmt_time(ts))
            else:
                row.update("", "")

    def _cfg(self, key: str, m) -> dict:
        ratio = f"{round(m.tokens_out / max(m.tokens_in, 1))}:1" if m.tokens_in else "—"
        avg_in  = format_compact(m.tokens_in  // 14) if m.tokens_in  else "—"
        avg_out = format_compact(m.tokens_out // 14) if m.tokens_out else "—"
        cost_pct = f"{round(m.cost_today / max(m.cost_all_time, 0.001) * 100)}% used" if m.cost_all_time else "0%"

        cfgs = {
            "tokens_in": dict(
                header="TOKENS IN", value=format_compact(m.tokens_in_today),
                desc=f"Output-to-input ratio at {ratio} — within normal operating range.",
                color=ACCENT_GREEN, badge="▲ 8%", badge_color=ACCENT_GREEN,
                chart_data=m.tokens_in_history,
                stats=[("Avg Completion", avg_in), ("I/O Ratio", ratio),
                       ("This Week", format_compact(m.tokens_in_this_week)), ("vs Yesterday", "+8%")],
            ),
            "tokens_out": dict(
                header="TOKENS OUT", value=format_compact(m.tokens_out_today),
                desc=f"Output-to-input ratio at {ratio} — within normal operating range.",
                color=ACCENT_BLUE, badge="▲ 8%", badge_color=ACCENT_GREEN,
                chart_data=m.tokens_out_history,
                stats=[("Avg Completion", avg_out), ("I/O Ratio", ratio),
                       ("This Week", format_compact(m.tokens_out_this_week)), ("vs Yesterday", "+8%")],
            ),
            "agents": dict(
                header="ACTIVE AGENTS", value=str(self._active_agents),
                desc=f"{self._active_agents} agent{'s' if self._active_agents != 1 else ''} live — monitoring in real time.",
                color=ACCENT_GREEN, badge="Stable", badge_color=ACCENT_GREEN,
                chart_data=m.sessions_history,
                stats=[("Running", str(self._active_agents)), ("Queued", "0"),
                       ("Total Tasks", str(m.sessions_total)), ("Avg Duration", "4m 12s")],
            ),
            "cost": dict(
                header="COST TODAY", value=format_usd(m.cost_today),
                desc=f"Projected daily spend ${m.cost_today * 1.2:.2f} — within the daily budget.",
                color=ACCENT_AMBER, badge=cost_pct, badge_color=ACCENT_AMBER,
                chart_data=m.cost_history,
                stats=[("Budget Used", cost_pct), ("Net Calls", str(m.sessions_total * 5)),
                       ("Projected", format_usd(m.cost_today * 1.2)), ("All-time", format_usd(m.cost_all_time))],
            ),
            "cache": dict(
                header="CACHE HIT RATE", value=format_cache_rate(m.cache_read, m.tokens_in),
                desc="Prompt cache reuse rate. Higher is better — reduces cost and latency.",
                color=ACCENT_TEAL, badge="Optimal", badge_color=ACCENT_TEAL,
                chart_data=[m.cache_read // max(m.jsonl_files, 1)] * 14 if m.cache_read else [],
                stats=[("Cache Reads", format_compact(m.cache_read)), ("Total Input", format_compact(m.tokens_in)),
                       ("Savings Est.", format_usd(m.cache_read * 0.000_001)), ("vs Yesterday", "—")],
            ),
            "version": dict(
                header="VERSION", value=self._version,
                desc=f"Running the latest stable release. {self._update_status}",
                color=ACCENT_TEAL, badge="Latest", badge_color=ACCENT_GREEN,
                chart_data=[],
                stats=[("Current", f"v{self._version}"), ("Last Updated", "2d ago"),
                       ("Channel", "Stable"), ("Next Check", "6h")],
            ),
            "sessions": dict(
                header="TOTAL SESSIONS", value=str(m.sessions_total),
                desc=f"{m.sessions_today} sessions today, on pace with your 14-day average.",
                color=ACCENT_GREEN, badge=f"▲ {m.sessions_today} today", badge_color=ACCENT_GREEN,
                chart_data=m.sessions_history,
                stats=[("Today", str(m.sessions_today)), ("This Week", str(m.sessions_this_week)),
                       ("All Time", str(m.sessions_total)), ("Avg / Day", str(max(1, m.sessions_total // 14)))],
            ),
            "config": dict(
                header="CONFIG FILE", value="Loaded",
                desc=f"Config: {CONFIG_PATH}",
                color=ACCENT_TEAL, badge="Valid", badge_color=ACCENT_TEAL,
                chart_data=[],
                stats=[("Path", "~/.agentwatch"), ("Source", "~/.claude"),
                       ("Poll", "1.0s"), ("Metrics", "2.0s")],
            ),
        }
        return cfgs.get(key, cfgs["tokens_in"])


# ─────────────────────────────────────────────────────────────────────────────
# AgentWatchPanel
# ─────────────────────────────────────────────────────────────────────────────
class AgentWatchPanel:
    def __init__(self, on_quit, on_check_update, on_restart, on_open_docs):
        import AppKit, objc
        self._visible = False
        self._on_quit = on_quit
        self._on_check_update = on_check_update
        self._on_restart = on_restart
        self._on_open_docs = on_open_docs
        self._btn_targets = []  # keep strong refs

        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskFullSizeContentView
        )
        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (PANEL_W, PANEL_H)), style,
            AppKit.NSBackingStoreBuffered, False,
        )
        self._panel.setTitle_("AgentWatch")
        self._panel.setTitlebarAppearsTransparent_(True)
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setLevel_(AppKit.NSFloatingWindowLevel)
        try:
            appearance = AppKit.NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            if appearance:
                self._panel.setAppearance_(appearance)
        except Exception:
            pass

        content = self._panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(_cgcolor(*BG_PANEL))

        left_frame  = ((0, FOOTER_H), (LEFT_W, PANEL_H - FOOTER_H))
        right_frame = ((LEFT_W + 1, FOOTER_H), (RIGHT_W - 1, PANEL_H - FOOTER_H))

        self._left  = LeftPanel(left_frame, self._on_metric_selected)
        self._right = RightPanel(right_frame)
        content.addSubview_(self._left.view)
        content.addSubview_(self._right.view)
        self._left.init_selection()

        # Divider
        div = AppKit.NSView.alloc().initWithFrame_(((LEFT_W, FOOTER_H), (1, PANEL_H - FOOTER_H)))
        div.setWantsLayer_(True)
        div.layer().setBackgroundColor_(_cgcolor(0.20, 0.20, 0.24))
        content.addSubview_(div)

        # Footer
        footer = AppKit.NSView.alloc().initWithFrame_(((0, 0), (PANEL_W, FOOTER_H)))
        footer.setWantsLayer_(True)
        footer.layer().setBackgroundColor_(_cgcolor(0.08, 0.08, 0.10))
        btn_configs = [
            ("Check for Updates", on_check_update),
            ("Restart",           lambda: on_restart(None)),
            ("Claude Docs",       lambda: on_open_docs(None)),
            ("Quit",              on_quit),
        ]
        btn_w = PANEL_W / len(btn_configs)
        for i, (title, cb) in enumerate(btn_configs):
            tgt = get_button_target_class().alloc().initWithCallback_(cb)
            self._btn_targets.append(tgt)
            btn = AppKit.NSButton.alloc().initWithFrame_(((i * btn_w, 1), (btn_w - 1, FOOTER_H - 2)))
            btn.setBezelStyle_(0)
            btn.setButtonType_(0)
            btn.setBordered_(False)
            btn.setFont_(_nsfont(10))
            btn.setWantsLayer_(True)
            _set_btn_text_color(btn, title, _nsfont(10), TEXT_SEC)
            btn.layer().setBackgroundColor_(_cgcolor(0.08, 0.08, 0.10))
            btn.setTarget_(tgt)
            btn.setAction_(objc.selector(tgt.buttonClicked_, selector=b"buttonClicked:", signature=b"v@:@"))
            footer.addSubview_(btn)
        content.addSubview_(footer)

        self._right.select_metric("tokens_in")

    def _on_metric_selected(self, key: str):
        self._right.select_metric(key)

    def show_near_status_bar(self):
        import AppKit
        screen = AppKit.NSScreen.mainScreen()
        if screen:
            sr = screen.frame()
            x = sr.size.width - PANEL_W - 20
            y = sr.size.height - PANEL_H - 30
            self._panel.setFrameOrigin_((x, y))
        self._panel.makeKeyAndOrderFront_(None)
        self._visible = True

    def hide(self):
        self._panel.orderOut_(None)
        self._visible = False

    def toggle(self):
        if self._visible:
            self.hide()
        else:
            self.show_near_status_bar()

    def update(self, status, active_agents, metrics, version, update_status):
        self._left.update_cards(status, active_agents, metrics)
        self._right.update(status, active_agents, metrics, version, update_status)


# ─────────────────────────────────────────────────────────────────────────────
# Notification helper
# ─────────────────────────────────────────────────────────────────────────────
def notify(title: str, message: str, sound: bool):
    sent = False
    try:
        import AppKit
        n = AppKit.NSUserNotification.alloc().init()
        n.setTitle_(title)
        n.setInformativeText_(message)
        logo = get_logo()
        if logo:
            n.set_identityImage_(logo)
        if sound:
            n.setSoundName_(AppKit.NSUserNotificationDefaultSoundName)
        center = AppKit.NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            raise RuntimeError("NSUserNotificationCenter unavailable (macOS 14+)")
        center.deliverNotification_(n)
        sent = True
    except Exception as exc:
        print(f"[AgentWatch] AppKit notification error: {exc}", file=sys.stderr)
    if not sent:
        try:
            safe_t = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_m = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe_m}" with title "{safe_t}"'],
                check=False, capture_output=True,
            )
        except Exception as exc2:
            print(f"[AgentWatch] osascript error: {exc2}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# AgentWatch rumps app
# ─────────────────────────────────────────────────────────────────────────────
class AgentWatch(rumps.App):
    def __init__(self):
        super().__init__(name="AgentWatch", title="", quit_button=None)
        self._config = load_config()
        self._status = "stopped"
        self._active_agents = 0
        self._prev_status = None
        self._pending = "stopped"
        self._pending_count = 0
        self._anim_frame = 0
        self._last_active_at = 0.0
        self._metrics = scan_metrics()
        self._current_version = get_version(os.path.dirname(os.path.abspath(sys.argv[0])))
        self._remote_version = None
        self._update_status = "Auto-update on"
        self._update_lock = threading.Lock()
        self._lock = threading.Lock()
        self._alerts = AlertManager(self._config, notify)
        self._icon_nsimage = make_icon("stopped")
        self._panel: Optional[AgentWatchPanel] = None
        self._panel_sig = None

        self.menu = [
            rumps.MenuItem("Open AgentWatch", callback=self._toggle_panel),
            None,
            rumps.MenuItem("Quit AgentWatch", callback=rumps.quit_application),
        ]

        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _set_icon(self, nsimage):
        self._icon_nsimage = nsimage
        self._nsapp.setStatusBarIcon()

    def _ensure_panel(self):
        if self._panel is None:
            self._panel = AgentWatchPanel(
                on_quit=rumps.quit_application,
                on_check_update=lambda: self._start_update_check(manual=True),
                on_restart=self._restart_app,
                on_open_docs=self._open_docs,
            )

    def _toggle_panel(self, _sender=None):
        just_created = self._panel is None
        self._ensure_panel()
        if just_created:
            # Force an immediate data push so the panel isn't blank on first open
            self._panel_sig = None
            self._push_to_panel()
        self._panel.toggle()

    def _push_to_panel(self):
        if self._panel is None:
            return
        sig = (
            self._status, self._active_agents,
            self._metrics.tokens_in_today, self._metrics.tokens_out_today,
            self._metrics.tokens_in, self._metrics.tokens_out,
            round(self._metrics.cost_today, 6), self._metrics.last_tool,
            self._metrics.sessions_today, self._current_version, self._update_status,
        )
        if sig == self._panel_sig:
            return
        self._panel_sig = sig
        self._panel.update(
            self._status, self._active_agents, self._metrics,
            self._current_version, self._update_status,
        )

    def _poll_loop(self):
        last_metrics_poll = 0.0
        while True:
            try:
                raw_status, agent_count = detect_process_state()
                now = time.monotonic()
                status_changed = False
                previous_status = None
                with self._lock:
                    self._active_agents = agent_count
                    if raw_status == "working":
                        self._status = "working"
                        self._pending = "working"
                        self._pending_count = DEBOUNCE_COUNT
                        self._last_active_at = now
                    else:
                        if (self._status == "working" and
                                now - self._last_active_at < float(self._config.get("working_hold_sec", WORKING_HOLD_SEC))):
                            pass
                        else:
                            if raw_status == self._pending:
                                self._pending_count += 1
                            else:
                                self._pending = raw_status
                                self._pending_count = 1
                            if self._pending_count >= DEBOUNCE_COUNT:
                                self._status = self._pending
                    if now - last_metrics_poll >= float(self._config.get("metrics_interval", METRICS_INTERVAL)):
                        self._metrics = scan_metrics()
                        last_metrics_poll = now
                    previous_status = self._prev_status
                    if previous_status != self._status:
                        status_changed = True
                        self._prev_status = self._status
                self._alerts.maybe_send_budget_alert(self._metrics)
                if status_changed and previous_status is not None:
                    self._alerts.handle_status_transition(previous_status, self._status, self._metrics)
            except Exception as exc:
                print(f"[AgentWatch] poll error: {exc}", file=sys.stderr)
            time.sleep(float(self._config.get("poll_interval", POLL_INTERVAL)))

    @rumps.timer(ANIM_INTERVAL)
    def _anim_tick(self, _sender):
        with self._lock:
            status = self._status
        if status == "working":
            self._anim_frame = (self._anim_frame + 1) % 12
            self._set_icon(make_icon("working", self._anim_frame))
            self._last_static = None
        else:
            if getattr(self, "_last_static", None) != status:
                self._last_static = status
                self._anim_frame = 0
                self._set_icon(make_icon(status))
        self._push_to_panel()

    def _start_update_check(self, manual=False):
        if not self._config.get("updates", {}).get("enabled", True) and not manual:
            return
        threading.Thread(target=self._run_update_check, kwargs={"manual": manual}, daemon=True).start()

    def _run_update_check(self, manual=False):
        if not self._update_lock.acquire(blocking=False):
            return
        try:
            repo_raw = str(self._config.get("updates", {}).get("repo_raw", REPO_RAW))
            install_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            with self._lock:
                self._update_status = "Checking..."
            remote_version = check_remote_version(repo_raw)
            with self._lock:
                self._remote_version = remote_version
            if not remote_version:
                with self._lock:
                    self._update_status = "Update check failed"
                return
            if remote_version == self._current_version:
                with self._lock:
                    self._update_status = "Up to date"
                if manual:
                    notify("AgentWatch", f"Already on {self._current_version}.", False)
                return
            with self._lock:
                self._update_status = f"Updating to {remote_version}..."
            result = apply_update(install_dir, repo_raw, self._current_version)
            if result.error:
                with self._lock:
                    self._update_status = "Update failed"
                notify("AgentWatch update failed", result.error, False)
                return
            if result.updated and result.version:
                with self._lock:
                    self._current_version = result.version
                    self._update_status = f"Updated to {result.version}"
                notify("AgentWatch updated", f"Updated to {result.version}. Restarting.", False)
                self._restart_app(None)
        finally:
            self._update_lock.release()

    def _auto_update_loop(self):
        interval = float(self._config.get("updates", {}).get("check_interval_sec", 300.0))
        while True:
            time.sleep(max(30.0, interval))
            self._start_update_check()

    def _open_docs(self, _sender=None):
        import webbrowser
        webbrowser.open("https://docs.anthropic.com/en/docs/claude-code/overview")

    def _restart_app(self, _sender=None):
        script_path = os.path.abspath(sys.argv[0])
        try:
            subprocess.Popen(
                [sys.executable, script_path, *sys.argv[1:]],
                cwd=os.path.dirname(script_path), start_new_session=True,
            )
        except Exception as exc:
            print(f"[AgentWatch] restart error: {exc}", file=sys.stderr)
            return
        rumps.quit_application()

    def _manual_update_check(self, _sender=None):
        self._start_update_check(manual=True)


def main():
    app = AgentWatch()
    if app._config.get("updates", {}).get("enabled", True):
        threading.Thread(target=app._auto_update_loop, daemon=True).start()
    app.run()

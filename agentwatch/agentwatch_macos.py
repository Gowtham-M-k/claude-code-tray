import math
import os
import subprocess
import sys
import threading
import time

import rumps

from agentwatch_alerts import AlertManager
from agentwatch_core import (
    ANIM_INTERVAL,
    CONFIG_PATH,
    CPU_THRESHOLD,
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

COLOR_WORKING = (0.20, 0.78, 0.35)
COLOR_IDLE = (0.95, 0.75, 0.10)
COLOR_STOPPED = (0.90, 0.25, 0.25)

_LOGO_SIZE = 22.0 * 0.75
_CLAUDE_LOGO = None


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


def make_icon(state: str, frame: int = 0):
    import AppKit
    import Quartz

    size = 22.0
    canvas = AppKit.NSImage.alloc().initWithSize_((size, size))
    canvas.lockFocus()

    ctx = AppKit.NSGraphicsContext.currentContext().CGContext()

    logo = get_logo()
    logo_x = (size - _LOGO_SIZE) / 2
    logo_y = (size - _LOGO_SIZE) / 2 + size * 0.04

    if logo is not None:
        logo.drawAtPoint_fromRect_operation_fraction_(
            (logo_x, logo_y),
            ((0, 0), (_LOGO_SIZE, _LOGO_SIZE)),
            AppKit.NSCompositeSourceOver,
            1.0,
        )
    else:
        Quartz.CGContextSetRGBFillColor(ctx, 0.85, 0.47, 0.34, 1.0)
        Quartz.CGContextAddArc(ctx, size / 2, size / 2, size * 0.36, 0, 2 * math.pi, 0)
        Quartz.CGContextFillPath(ctx)

    cr, cg, cb = {
        "working": COLOR_WORKING,
        "idle": COLOR_IDLE,
        "stopped": COLOR_STOPPED,
    }[state]

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
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, 0, 2 * math.pi, 0)
        Quartz.CGContextStrokePath(ctx)

        start_angle = (frame * 30) * math.pi / 180
        end_angle = start_angle + 1.5 * math.pi
        Quartz.CGContextSetRGBStrokeColor(ctx, cr, cg, cb, 1.0)
        Quartz.CGContextSetLineWidth(ctx, arc_stroke)
        Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, track_r, start_angle, end_angle, 0)
        Quartz.CGContextStrokePath(ctx)

    Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r + 1.0, 0, 2 * math.pi, 0)
    Quartz.CGContextFillPath(ctx)

    Quartz.CGContextSetRGBFillColor(ctx, cr, cg, cb, 1.0)
    Quartz.CGContextAddArc(ctx, dot_cx, dot_cy, dot_r, 0, 2 * math.pi, 0)
    Quartz.CGContextFillPath(ctx)

    canvas.unlockFocus()
    canvas.setTemplate_(False)
    return canvas


def _apply_attributed(item: rumps.MenuItem, title: str, attrs: dict):
    import AppKit
    attributed = AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs)
    item._menuitem.setAttributedTitle_(attributed)


def _bold_white_attrs():
    import AppKit
    return {
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(14.0),
    }


def _white_attrs():
    import AppKit
    return {AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()}


def _dim_attrs():
    import AppKit
    return {
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.50, 0.50, 0.50, 1.0
        ),
        AppKit.NSFontAttributeName: AppKit.NSFont.menuFontOfSize_(11.5),
    }


def _header_attrs():
    import AppKit
    return {
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.38, 0.38, 0.38, 1.0
        ),
        AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(9.0),
        AppKit.NSKernAttributeName: 1.8,
    }


def _utf16_len(s: str) -> int:
    """Length of string in UTF-16 code units (what NSRange expects)."""
    return len(s.encode("utf-16-le")) // 2


def _style_two_col(item: rumps.MenuItem, label: str, value: str, value_color=None):
    """Dim gray label on left, white (or colored) value on right."""
    import AppKit
    PAD = 26
    text = f"{label:<{PAD}}{value}"
    full_attrs = {
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
        AppKit.NSFontAttributeName: AppKit.NSFont.menuFontOfSize_(12.0),
    }
    attributed = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
        text, full_attrs
    )
    dim_color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.50, 0.50, 0.50, 1.0)
    label_utf16_len = _utf16_len(label)
    label_range = AppKit.NSRange(0, label_utf16_len)
    attributed.addAttribute_value_range_(
        AppKit.NSForegroundColorAttributeName, dim_color, label_range
    )
    if value_color is not None:
        value_start = _utf16_len(f"{label:<{PAD}}")
        value_len = _utf16_len(value)
        value_range = AppKit.NSRange(value_start, value_len)
        attributed.addAttribute_value_range_(
            AppKit.NSForegroundColorAttributeName, value_color, value_range
        )
    item._menuitem.setAttributedTitle_(attributed)


def _green_color():
    import AppKit
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.78, 0.35, 1.0)


def _yellow_color():
    import AppKit
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.75, 0.10, 1.0)


def _red_color():
    import AppKit
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.90, 0.30, 0.25, 1.0)


def make_header(title: str):
    item = rumps.MenuItem(title.upper())
    item.set_callback(None)
    _apply_attributed(item, title.upper(), _header_attrs())
    return item


def notify(title: str, message: str, sound: bool):
    sent = False
    try:
        import AppKit

        notification = AppKit.NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)

        # set_identityImage_ places the icon in the app-icon slot (top-left)
        logo = get_logo()
        if logo:
            notification.set_identityImage_(logo)

        if sound:
            notification.setSoundName_(AppKit.NSUserNotificationDefaultSoundName)

        center = AppKit.NSUserNotificationCenter.defaultUserNotificationCenter()
        center.deliverNotification_(notification)
        sent = True
    except Exception as exc:
        print(f"[AgentWatch] AppKit notification error: {exc}", file=sys.stderr)

    if not sent:
        # Fallback: osascript (no icon support, but always works as last resort)
        try:
            import subprocess

            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{safe_msg}" with title "{safe_title}"',
                ],
                check=False,
                capture_output=True,
            )
        except Exception as exc2:
            print(
                f"[AgentWatch] osascript notification error: {exc2}",
                file=sys.stderr,
            )


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
        self._last_menu_signature = None
        self._current_version = get_version(os.path.dirname(os.path.abspath(sys.argv[0])))
        self._remote_version = None
        self._update_status = "Auto-update on"
        self._update_lock = threading.Lock()
        self._lock = threading.Lock()
        self._alerts = AlertManager(self._config, notify)
        self._icon_nsimage = make_icon("stopped")

        self._status_item = rumps.MenuItem(STATE_LABEL["stopped"])
        self._status_item.set_callback(None)
        self._summary_item = rumps.MenuItem("No data yet")
        self._summary_item.set_callback(None)
        self._agents_item = rumps.MenuItem("Active agents")
        self._agents_item.set_callback(None)
        self._tokens_in_item = rumps.MenuItem("Tokens in")
        self._tokens_in_item.set_callback(None)
        self._tokens_out_item = rumps.MenuItem("Tokens out")
        self._tokens_out_item.set_callback(None)
        self._tokens_in_total_item = rumps.MenuItem("Tokens in (total)")
        self._tokens_in_total_item.set_callback(None)
        self._tokens_out_total_item = rumps.MenuItem("Tokens out (total)")
        self._tokens_out_total_item.set_callback(None)
        self._cache_item = rumps.MenuItem("Cache hit rate")
        self._cache_item.set_callback(None)
        self._cost_today_item = rumps.MenuItem("Cost today")
        self._cost_today_item.set_callback(None)
        self._cost_all_time_item = rumps.MenuItem("Cost all-time")
        self._cost_all_time_item.set_callback(None)
        self._last_tool_item = rumps.MenuItem("Last tool")
        self._last_tool_item.set_callback(None)
        self._budget_item = rumps.MenuItem("Daily budget")
        self._budget_item.set_callback(None)
        self._version_item = rumps.MenuItem(f"Version: {self._current_version}")
        self._version_item.set_callback(None)
        self._update_item = rumps.MenuItem(self._update_status)
        self._update_item.set_callback(None)
        self._files_item = rumps.MenuItem("Sessions")
        self._files_item.set_callback(None)
        self._config_item = rumps.MenuItem(f"Config: {CONFIG_PATH}")
        self._config_item.set_callback(None)
        self._source_item = rumps.MenuItem(f"Source: {JSONL_GLOB}")
        self._source_item.set_callback(None)

        self.menu = [
            self._status_item,
            self._summary_item,
            None,
            make_header("Usage  —  Today"),
            self._agents_item,
            self._tokens_in_item,
            self._tokens_out_item,
            None,
            make_header("Usage  —  All-time"),
            self._tokens_in_total_item,
            self._tokens_out_total_item,
            self._cache_item,
            None,
            make_header("Cost"),
            self._cost_today_item,
            self._cost_all_time_item,
            self._budget_item,
            None,
            make_header("Activity"),
            self._last_tool_item,
            self._files_item,
            None,
            make_header("System"),
            self._version_item,
            self._update_item,
            self._config_item,
            self._source_item,
            None,
            rumps.MenuItem("Check for Updates", callback=self._manual_update_check),
            rumps.MenuItem("Restart AgentWatch", callback=self._restart_app),
            rumps.MenuItem("Open Claude Docs", callback=self._open_docs),
            None,
            rumps.MenuItem("Quit AgentWatch", callback=rumps.quit_application),
        ]

        self._refresh_menu_items()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _set_icon(self, nsimage):
        self._icon_nsimage = nsimage
        self._nsapp.setStatusBarIcon()

    def _menu_signature(self):
        return (
            self._status,
            self._active_agents,
            self._metrics.has_data,
            self._metrics.tokens_in_today,
            self._metrics.tokens_out_today,
            self._metrics.tokens_in,
            self._metrics.tokens_out,
            self._metrics.cache_read,
            round(self._metrics.cost_today, 6),
            round(self._metrics.cost_all_time, 6),
            self._metrics.last_tool,
            self._metrics.jsonl_files,
            self._metrics.latest_session_slug,
            self._metrics.latest_session_id,
            self._metrics.latest_user_text,
            self._current_version,
            self._remote_version,
            self._update_status,
            round(float(self._config["alerts"]["daily_budget_usd"]), 6),
        )

    def _refresh_menu_items(self):
        signature = self._menu_signature()
        if signature == self._last_menu_signature:
            return
        self._last_menu_signature = signature

        # Status — bold white, larger
        status_text = STATE_LABEL[self._status]
        self._status_item.title = status_text
        _apply_attributed(self._status_item, status_text, _bold_white_attrs())

        # Summary — dim gray, smaller
        summary_text = make_summary(self._status, self._active_agents, self._metrics)
        self._summary_item.title = summary_text
        _apply_attributed(self._summary_item, summary_text, _dim_attrs())

        # Agent count — green when active
        agent_color = _green_color() if self._active_agents > 0 else None
        _style_two_col(self._agents_item, "Active agents", str(self._active_agents), agent_color)

        # Sessions / version / update / budget
        _style_two_col(self._files_item, "Sessions", str(self._metrics.jsonl_files))
        _style_two_col(self._version_item, "Version", self._current_version)
        _style_two_col(self._update_item, "Auto-update", self._update_status)
        _style_two_col(
            self._budget_item,
            "Daily budget",
            format_usd(float(self._config["alerts"]["daily_budget_usd"])),
        )

        # Config / source — dim gray
        config_text = f"Config: {CONFIG_PATH}"
        self._config_item.title = config_text
        _apply_attributed(self._config_item, config_text, _dim_attrs())

        source_text = f"Source: {JSONL_GLOB}"
        self._source_item.title = source_text
        _apply_attributed(self._source_item, source_text, _dim_attrs())

        if self._metrics.has_data:
            _style_two_col(
                self._tokens_in_item,
                "Tokens in",
                format_compact(self._metrics.tokens_in_today),
                _green_color(),
            )
            _style_two_col(
                self._tokens_out_item,
                "Tokens out",
                format_compact(self._metrics.tokens_out_today),
            )
            _style_two_col(
                self._tokens_in_total_item,
                "Tokens in",
                format_compact(self._metrics.tokens_in),
                _green_color(),
            )
            _style_two_col(
                self._tokens_out_total_item,
                "Tokens out",
                format_compact(self._metrics.tokens_out),
            )
            cache_rate_str = format_cache_rate(self._metrics.cache_read, self._metrics.tokens_in)
            cache_color = _green_color() if self._metrics.cache_read > 0 else None
            _style_two_col(self._cache_item, "Cache hit rate", cache_rate_str, cache_color)
            _style_two_col(
                self._cost_today_item,
                "Cost today",
                format_usd(self._metrics.cost_today),
                _yellow_color(),
            )
            _style_two_col(
                self._cost_all_time_item,
                "Cost all-time",
                format_usd(self._metrics.cost_all_time),
            )
            _style_two_col(
                self._last_tool_item,
                "Last tool",
                self._metrics.last_tool or NO_DATA_LABEL,
            )
        else:
            _style_two_col(self._tokens_in_item, "Tokens in", NO_DATA_LABEL)
            _style_two_col(self._tokens_out_item, "Tokens out", NO_DATA_LABEL)
            _style_two_col(self._tokens_in_total_item, "Tokens in", NO_DATA_LABEL)
            _style_two_col(self._tokens_out_total_item, "Tokens out", NO_DATA_LABEL)
            _style_two_col(self._cache_item, "Cache hit rate", NO_DATA_LABEL)
            _style_two_col(self._cost_today_item, "Cost today", NO_DATA_LABEL)
            _style_two_col(self._cost_all_time_item, "Cost all-time", NO_DATA_LABEL)
            _style_two_col(self._last_tool_item, "Last tool", NO_DATA_LABEL)

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
                        if (
                            self._status == "working"
                            and now - self._last_active_at
                            < float(self._config.get("working_hold_sec", WORKING_HOLD_SEC))
                        ):
                            pass
                        else:
                            if raw_status == self._pending:
                                self._pending_count += 1
                            else:
                                self._pending = raw_status
                                self._pending_count = 1
                            if self._pending_count >= DEBOUNCE_COUNT:
                                self._status = self._pending

                    if now - last_metrics_poll >= float(
                        self._config.get("metrics_interval", METRICS_INTERVAL)
                    ):
                        self._metrics = scan_metrics()
                        last_metrics_poll = now
                    previous_status = self._prev_status
                    if previous_status != self._status:
                        status_changed = True
                        self._prev_status = self._status

                self._alerts.maybe_send_budget_alert(self._metrics)
                if status_changed and previous_status is not None:
                    self._alerts.handle_status_transition(
                        previous_status,
                        self._status,
                        self._metrics,
                    )
            except Exception as exc:
                print(f"[AgentWatch] poll error: {exc}", file=sys.stderr)

            time.sleep(float(self._config.get("poll_interval", POLL_INTERVAL)))

    def _start_update_check(self, manual: bool = False):
        if not self._config.get("updates", {}).get("enabled", True) and not manual:
            return
        thread = threading.Thread(
            target=self._run_update_check,
            kwargs={"manual": manual},
            daemon=True,
        )
        thread.start()

    def _run_update_check(self, manual: bool = False):
        if not self._update_lock.acquire(blocking=False):
            return
        try:
            repo_raw = str(self._config.get("updates", {}).get("repo_raw", REPO_RAW))
            install_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            with self._lock:
                self._update_status = "Checking for updates..."

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
                    notify("AgentWatch update", f"Already on {self._current_version}.", False)
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
                    self._remote_version = result.version
                    self._update_status = f"Updated to {result.version}"
                notify(
                    "AgentWatch updated",
                    f"Updated to {result.version}. Restarting AgentWatch.",
                    False,
                )
                self._restart_app(None)
        finally:
            self._update_lock.release()

    def _auto_update_loop(self):
        interval = float(
            self._config.get("updates", {}).get("check_interval_sec", 300.0)
        )
        while True:
            time.sleep(max(30.0, interval))
            self._start_update_check()

    @rumps.timer(ANIM_INTERVAL)
    def _anim_tick(self, _sender):
        with self._lock:
            status = self._status
            self._refresh_menu_items()

        if status == "working":
            self._anim_frame = (self._anim_frame + 1) % 12
            self._set_icon(make_icon("working", self._anim_frame))
            self._last_static = None
        else:
            if getattr(self, "_last_static", None) != status:
                self._last_static = status
                self._anim_frame = 0
                self._set_icon(make_icon(status))

    def _open_docs(self, _sender):
        import webbrowser

        webbrowser.open("https://docs.anthropic.com/en/docs/claude-code/overview")

    def _restart_app(self, _sender):
        script_path = os.path.abspath(sys.argv[0])
        argv = [sys.executable, script_path, *sys.argv[1:]]
        workdir = os.path.dirname(script_path)
        try:
            subprocess.Popen(
                argv,
                cwd=workdir,
                start_new_session=True,
            )
        except Exception as exc:
            print(f"[AgentWatch] restart error: {exc}", file=sys.stderr)
            return
        rumps.quit_application()

    def _manual_update_check(self, _sender):
        self._start_update_check(manual=True)


def main():
    app = AgentWatch()
    if app._config.get("updates", {}).get("enabled", True):
        threading.Thread(target=app._auto_update_loop, daemon=True).start()
    app.run()

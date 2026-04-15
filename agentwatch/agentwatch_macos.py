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


def make_header(title: str):
    item = rumps.MenuItem(title)
    item.set_callback(None)
    return item


def notify(title: str, message: str, sound: bool):
    try:
        rumps.notification("AgentWatch", title, message)
    except Exception as exc:
        print(f"[AgentWatch] notification error: {exc}", file=sys.stderr)
    if sound:
        try:
            import AppKit

            AppKit.NSBeep()
        except Exception:
            pass


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
        self._summary_item = rumps.MenuItem("Running - 0 agents - No data yet")
        self._summary_item.set_callback(None)
        self._agents_item = rumps.MenuItem("⚡  Active agents: 0")
        self._agents_item.set_callback(None)
        self._tokens_in_item = rumps.MenuItem(f"↑  Tokens in: {NO_DATA_LABEL}")
        self._tokens_out_item = rumps.MenuItem(f"↓  Tokens out: {NO_DATA_LABEL}")
        self._cache_item = rumps.MenuItem(f"⚡  Cache hit rate: {NO_DATA_LABEL}")
        self._cost_today_item = rumps.MenuItem(f"💵  Cost today: {NO_DATA_LABEL}")
        self._cost_all_time_item = rumps.MenuItem(f"💰  Cost all-time: {NO_DATA_LABEL}")
        self._last_tool_item = rumps.MenuItem(f"🔧  Last tool: {NO_DATA_LABEL}")
        self._budget_item = rumps.MenuItem("🚨  Daily budget: $5.00")
        self._budget_item.set_callback(None)
        self._version_item = rumps.MenuItem(f"🆕  Version: {self._current_version}")
        self._version_item.set_callback(None)
        self._update_item = rumps.MenuItem(self._update_status)
        self._update_item.set_callback(None)
        self._files_item = rumps.MenuItem("📁  JSONL files: 0")
        self._files_item.set_callback(None)
        self._config_item = rumps.MenuItem(f"⚙️  Config: {CONFIG_PATH}")
        self._config_item.set_callback(None)
        self._source_item = rumps.MenuItem(f"📁  Source: {JSONL_GLOB}")
        self._source_item.set_callback(None)

        self.menu = [
            self._status_item,
            self._summary_item,
            self._agents_item,
            None,
            make_header("Tokens"),
            self._tokens_in_item,
            self._tokens_out_item,
            None,
            make_header("Efficiency"),
            self._cache_item,
            None,
            make_header("Cost"),
            self._cost_today_item,
            self._cost_all_time_item,
            self._budget_item,
            None,
            make_header("Activity"),
            self._last_tool_item,
            self._version_item,
            self._update_item,
            self._files_item,
            self._config_item,
            self._source_item,
            None,
            rumps.MenuItem("Check for updates now", callback=self._manual_update_check),
            None,
            rumps.MenuItem("Restart AgentWatch", callback=self._restart_app),
            None,
            rumps.MenuItem("Open Claude Code docs", callback=self._open_docs),
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

        self._status_item.title = STATE_LABEL[self._status]
        self._summary_item.title = make_summary(
            self._status, self._active_agents, self._metrics
        )
        self._agents_item.title = f"⚡  Active agents: {self._active_agents}"
        self._files_item.title = f"📁  JSONL files: {self._metrics.jsonl_files}"
        self._version_item.title = f"🆕  Version: {self._current_version}"
        self._update_item.title = f"🔄  {self._update_status}"
        self._budget_item.title = (
            f"🚨  Daily budget: {format_usd(float(self._config['alerts']['daily_budget_usd']))}"
        )

        if self._metrics.has_data:
            self._tokens_in_item.title = (
                f"↑  Tokens in: {format_compact(self._metrics.tokens_in)}"
            )
            self._tokens_out_item.title = (
                f"↓  Tokens out: {format_compact(self._metrics.tokens_out)}"
            )
            self._cache_item.title = (
                "⚡  Cache hit rate: "
                f"{format_cache_rate(self._metrics.cache_read, self._metrics.tokens_in)}"
            )
            self._cost_today_item.title = (
                f"💵  Cost today: {format_usd(self._metrics.cost_today)}"
            )
            self._cost_all_time_item.title = (
                f"💰  Cost all-time: {format_usd(self._metrics.cost_all_time)}"
            )
            self._last_tool_item.title = (
                f"🔧  Last tool: {self._metrics.last_tool or NO_DATA_LABEL}"
            )
        else:
            self._tokens_in_item.title = f"↑  Tokens in: {NO_DATA_LABEL}"
            self._tokens_out_item.title = f"↓  Tokens out: {NO_DATA_LABEL}"
            self._cache_item.title = f"⚡  Cache hit rate: {NO_DATA_LABEL}"
            self._cost_today_item.title = f"💵  Cost today: {NO_DATA_LABEL}"
            self._cost_all_time_item.title = f"💰  Cost all-time: {NO_DATA_LABEL}"
            self._last_tool_item.title = f"🔧  Last tool: {NO_DATA_LABEL}"

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

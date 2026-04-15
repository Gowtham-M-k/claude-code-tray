import time

from agentwatch_core import (
    format_question_preview,
    format_session_title,
    format_usd,
    today_local,
)

_COOLDOWN_SEC = 60.0  # minimum seconds between identical notifications


class AlertManager:
    def __init__(self, config: dict, notifier):
        self._config = config
        self._notifier = notifier
        self._last_budget_alert_day = None
        self._last_sent: dict[str, float] = {}  # title → monotonic time

    def _send(self, title: str, message: str, sound: bool) -> None:
        now = time.monotonic()
        if now - self._last_sent.get(title, 0.0) < _COOLDOWN_SEC:
            return
        self._last_sent[title] = now
        self._notifier(title, message, sound)

    def maybe_send_budget_alert(self, metrics):
        alerts = self._config["alerts"]
        if not alerts.get("daily_budget", True):
            return
        budget = float(alerts.get("daily_budget_usd", 5.0) or 0.0)
        if budget <= 0:
            return

        today = today_local()
        if self._last_budget_alert_day != today and metrics.cost_today >= budget:
            self._last_budget_alert_day = today
            self._send(
                "Daily budget exceeded",
                (
                    f"Today's cost is {format_usd(metrics.cost_today)} "
                    f"against a {format_usd(budget)} budget."
                ),
                bool(alerts.get("sound", True)),
            )
        elif self._last_budget_alert_day and self._last_budget_alert_day != today:
            self._last_budget_alert_day = None

    def handle_status_transition(self, previous_status: str, current_status: str, metrics):
        if previous_status == current_status:
            return

        alerts = self._config["alerts"]
        sound = bool(alerts.get("sound", True))

        if (
            previous_status == "working"
            and current_status == "idle"
            and alerts.get("task_complete", True)
        ):
            self._send(
                f"Task complete — {format_session_title(metrics)}",
                format_question_preview(metrics.latest_user_text, max_lines=2),
                sound,
            )

        if (
            previous_status in {"working", "idle"}
            and current_status == "stopped"
            and alerts.get("agent_stopped", True)
        ):
            self._send(
                "Agent stopped",
                "Claude Code is no longer running on this machine.",
                sound,
            )

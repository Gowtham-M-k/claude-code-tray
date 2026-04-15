import glob
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psutil

try:
    import tomllib
except ImportError:
    tomllib = None
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

POLL_INTERVAL = 1.0
METRICS_INTERVAL = 2.0
CPU_THRESHOLD = 4.0
CPU_SAMPLE_TIME = 0.1
ANIM_INTERVAL = 0.12
DEBOUNCE_COUNT = 3
WORKING_HOLD_SEC = 4.0
AUTO_UPDATE_INTERVAL_SEC = 86400.0

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
JSONL_GLOB = os.path.join(CLAUDE_PROJECTS_DIR, "**", "*.jsonl")
CONFIG_PATH = os.path.expanduser("~/.agentwatch.toml")
NO_DATA_LABEL = "No data yet"
REPO_RAW = "https://raw.githubusercontent.com/Gowtham-M-k/claude-code-tray/main/agentwatch"
VERSION_FILENAME = "VERSION"
REMOTE_FILES = [
    "VERSION",
    "agentwatch.py",
    "agentwatch_mac.py",
    "agentwatch_macos.py",
    "agentwatch_core.py",
    "agentwatch_alerts.py",
    "agentwatch_updater.py",
    "agentwatch.example.toml",
    "claude-color.svg",
]

DEFAULT_CONFIG = {
    "poll_interval": POLL_INTERVAL,
    "metrics_interval": METRICS_INTERVAL,
    "working_hold_sec": WORKING_HOLD_SEC,
    "updates": {
        "enabled": True,
        "check_interval_sec": AUTO_UPDATE_INTERVAL_SEC,
        "repo_raw": REPO_RAW,
    },
    "alerts": {
        "task_complete": True,
        "agent_stopped": True,
        "daily_budget": True,
        "sound": True,
        "daily_budget_usd": 5.0,
    },
}

STATE_LABEL = {
    "working": "🟢  Working",
    "idle": "🟡  Idle — waiting for input",
    "stopped": "🔴  Not running",
}

CLAUDE_BINARY_NAMES = {"claude"}
CLAUDE_CMDLINE_HINTS = ["@anthropic-ai/claude-code", "claude-code"]


@dataclass
class MetricsSnapshot:
    has_data: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_in_today: int = 0
    tokens_out_today: int = 0
    cache_read: int = 0
    cost_today: float = 0.0
    cost_all_time: float = 0.0
    last_tool: Optional[str] = None
    jsonl_files: int = 0
    latest_session_slug: Optional[str] = None
    latest_session_id: Optional[str] = None
    latest_user_text: Optional[str] = None
    latest_user_timestamp: str = ""
    # Per-day history (last 14 days, oldest first) for sparklines
    tokens_in_history: list = None   # list of int
    tokens_out_history: list = None  # list of int
    cost_history: list = None        # list of float
    sessions_history: list = None    # list of int (sessions per day)
    # Weekly aggregates
    tokens_in_this_week: int = 0
    tokens_out_this_week: int = 0
    cost_this_week: float = 0.0
    sessions_today: int = 0
    sessions_total: int = 0
    sessions_this_week: int = 0
    # Recent tool actions (list of (timestamp_str, tool_name))
    recent_actions: list = None      # list of (ts, name)

    def __post_init__(self):
        if self.tokens_in_history is None:
            self.tokens_in_history = []
        if self.tokens_out_history is None:
            self.tokens_out_history = []
        if self.cost_history is None:
            self.cost_history = []
        if self.sessions_history is None:
            self.sessions_history = []
        if self.recent_actions is None:
            self.recent_actions = []


def format_compact(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def format_usd(value: float) -> str:
    return f"${value:.2f}"


def format_cache_rate(read_tokens: int, input_tokens: int) -> str:
    denom = input_tokens + read_tokens
    if denom <= 0:
        return "0%"
    return f"{round((read_tokens / denom) * 100):d}%"


def get_version(version_dir: Optional[str] = None) -> str:
    base_dir = version_dir or os.path.dirname(os.path.abspath(__file__))
    version_path = os.path.join(base_dir, VERSION_FILENAME)
    try:
        with open(version_path, "r", encoding="utf-8") as handle:
            return handle.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def format_session_title(metrics: MetricsSnapshot) -> str:
    if metrics.latest_session_slug:
        return metrics.latest_session_slug
    if metrics.latest_session_id:
        return metrics.latest_session_id[:8]
    return "Claude session"


def format_question_preview(text: Optional[str], max_lines: int = 2, line_width: int = 72) -> str:
    if not text:
        return "Claude Code is idle and waiting for your input."

    cleaned_lines = []
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.strip().split())
        if line:
            cleaned_lines.append(line)

    if not cleaned_lines:
        return "Claude Code is idle and waiting for your input."

    preview_lines = []
    truncated = False
    for line in cleaned_lines:
        while len(line) > line_width and len(preview_lines) < max_lines:
            split_at = line.rfind(" ", 0, line_width + 1)
            if split_at <= 0:
                split_at = line_width
            preview_lines.append(line[:split_at].rstrip())
            line = line[split_at:].lstrip()
            if len(preview_lines) >= max_lines and line:
                truncated = True
        if len(preview_lines) < max_lines and line:
            preview_lines.append(line)
        elif line:
            truncated = True
        if len(preview_lines) >= max_lines:
            if line and len(preview_lines) >= max_lines:
                truncated = True
            break

    if len(preview_lines) > max_lines:
        preview_lines = preview_lines[:max_lines]
    if truncated and preview_lines:
        if not preview_lines[-1].endswith("..."):
            preview_lines[-1] = preview_lines[-1][: max(0, line_width - 3)].rstrip() + "..."

    return "\n".join(preview_lines[:max_lines])


def make_summary(status: str, active_agents: int, metrics: MetricsSnapshot) -> str:
    status_word = {
        "working": "Running",
        "idle": "Idle",
        "stopped": "Stopped",
    }[status]
    if not metrics.has_data:
        return f"{status_word}  ·  No data yet"
    agents = f"{active_agents} active" if active_agents > 0 else "No agents"
    return (
        f"{status_word}  ·  {agents}  ·  "
        f"↑{format_compact(metrics.tokens_in_today)} "
        f"↓{format_compact(metrics.tokens_out_today)}"
    )


def deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config():
    config = deep_merge({}, DEFAULT_CONFIG)
    if tomllib is None:
        return config
    try:
        with open(CONFIG_PATH, "rb") as handle:
            user_config = tomllib.load(handle)
    except FileNotFoundError:
        return config
    except (OSError, ValueError, TypeError) as exc:
        print(f"[AgentWatch] config load error: {exc}", file=sys.stderr)
        return config
    if isinstance(user_config, dict):
        return deep_merge(config, user_config)
    return config


def first_present(record: dict, *paths):
    for path in paths:
        node = record
        found = True
        for key in path:
            if not isinstance(node, dict) or key not in node:
                found = False
                break
            node = node[key]
        if found:
            return node
    return None


def extract_message(record: dict):
    return first_present(record, ("message",), ("data", "message", "message"))


def extract_timestamp(record: dict) -> str:
    for path in (
        ("timestamp",),
        ("message", "timestamp"),
        ("data", "message", "timestamp"),
    ):
        value = first_present(record, path)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_session_slug(record: dict) -> Optional[str]:
    slug = record.get("slug")
    if isinstance(slug, str) and slug:
        return slug
    return None


def extract_session_id(record: dict) -> Optional[str]:
    session_id = record.get("sessionId")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def extract_user_text(record: dict) -> Optional[str]:
    message = extract_message(record)
    if not isinstance(message, dict):
        return None
    if message.get("role") != "user":
        return None

    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        return text or None

    parts = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    if parts:
        return "\n".join(parts)

    prompt = record.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None


def today_local() -> str:
    return datetime.now().date().isoformat()


def is_claude_root(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
        if name in CLAUDE_BINARY_NAMES:
            return True

        cmdline = proc.cmdline() or []
        if cmdline:
            exe = cmdline[0].lower()
            if exe.endswith("/claude") or exe == "claude":
                return True
            full = " ".join(cmdline).lower()
            if any(hint in full for hint in CLAUDE_CMDLINE_HINTS):
                return True
        return False
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def detect_process_state():
    all_procs = list(psutil.process_iter(["pid", "name"]))
    candidates = [p for p in all_procs if is_claude_root(p)]
    if not candidates:
        return "stopped", 0

    for proc in candidates:
        try:
            children = proc.children(recursive=True)
            if children:
                return "working", len(candidates)
            if proc.cpu_percent(interval=CPU_SAMPLE_TIME) >= CPU_THRESHOLD:
                return "working", len(candidates)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return "idle", len(candidates)


def _date_range(days: int) -> list:
    """Return list of ISO date strings for the last `days` days, oldest first."""
    from datetime import timedelta
    today = datetime.now().date()
    return [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]


def scan_metrics():
    metrics = MetricsSnapshot()
    latest_tool = ("", None)
    all_tool_actions = []  # list of (timestamp, tool_name)

    try:
        files = glob.glob(JSONL_GLOB, recursive=True)
    except OSError:
        files = []

    metrics.jsonl_files = len(files)
    if not files:
        return metrics

    metrics.has_data = True
    today = today_local()

    # Per-day accumulators for history (14 days)
    HISTORY_DAYS = 14
    day_labels = _date_range(HISTORY_DAYS)
    day_set = set(day_labels)

    from datetime import date as _date, timedelta as _td
    today_date = _date.today()
    week_start = (today_date - _td(days=today_date.weekday())).isoformat()

    # day -> {tokens_in, tokens_out, cost, sessions}
    per_day: dict = {d: {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "sessions": set()} for d in day_labels}

    # sessions per day (all-time count)
    all_sessions: dict = {}  # date -> set of session ids

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    timestamp = extract_timestamp(record)
                    day = timestamp[:10] if timestamp else ""
                    message = extract_message(record)
                    session_id = extract_session_id(record) or extract_session_slug(record)

                    usage = message.get("usage") if isinstance(message, dict) else None
                    if isinstance(usage, dict):
                        tin = int(usage.get("input_tokens", 0) or 0)
                        tout = int(usage.get("output_tokens", 0) or 0)
                        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
                        metrics.tokens_in += tin
                        metrics.tokens_out += tout
                        metrics.cache_read += cr
                        if day == today:
                            metrics.tokens_in_today += tin
                            metrics.tokens_out_today += tout
                        if day >= week_start:
                            metrics.tokens_in_this_week += tin
                            metrics.tokens_out_this_week += tout
                        if day in day_set:
                            per_day[day]["tokens_in"] += tin
                            per_day[day]["tokens_out"] += tout

                    cost = first_present(
                        record,
                        ("costUSD",),
                        ("message", "costUSD"),
                        ("data", "message", "costUSD"),
                        ("data", "message", "message", "costUSD"),
                    )
                    if isinstance(cost, (int, float)):
                        cost_value = float(cost)
                        metrics.cost_all_time += cost_value
                        if day == today:
                            metrics.cost_today += cost_value
                        if day >= week_start:
                            metrics.cost_this_week += cost_value
                        if day in day_set:
                            per_day[day]["cost"] += cost_value

                    if isinstance(message, dict):
                        for block in message.get("content") or []:
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_use"
                                and block.get("name")
                            ):
                                name = str(block["name"])
                                all_tool_actions.append((timestamp, name))
                                if timestamp >= latest_tool[0]:
                                    latest_tool = (timestamp, name)

                    user_text = extract_user_text(record)
                    if user_text:
                        if timestamp >= metrics.latest_user_timestamp:
                            metrics.latest_user_timestamp = timestamp
                            metrics.latest_user_text = user_text
                            metrics.latest_session_slug = extract_session_slug(record)
                            metrics.latest_session_id = extract_session_id(record)

                    # Session tracking
                    if session_id and day:
                        if day not in all_sessions:
                            all_sessions[day] = set()
                        all_sessions[day].add(session_id)
                        if day in day_set:
                            per_day[day]["sessions"].add(session_id)

        except (OSError, UnicodeDecodeError):
            continue

    metrics.last_tool = latest_tool[1]

    # Populate history arrays (oldest to newest)
    metrics.tokens_in_history = [per_day[d]["tokens_in"] for d in day_labels]
    metrics.tokens_out_history = [per_day[d]["tokens_out"] for d in day_labels]
    metrics.cost_history = [per_day[d]["cost"] for d in day_labels]
    metrics.sessions_history = [len(per_day[d]["sessions"]) for d in day_labels]

    # Session counts
    metrics.sessions_today = len(all_sessions.get(today, set()))
    metrics.sessions_total = sum(len(v) for v in all_sessions.values())
    metrics.sessions_this_week = sum(
        len(v) for k, v in all_sessions.items() if k >= week_start
    )

    # Recent actions: last 10, newest first
    all_tool_actions.sort(key=lambda x: x[0], reverse=True)
    metrics.recent_actions = all_tool_actions[:10]

    return metrics

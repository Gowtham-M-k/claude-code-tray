# AgentWatch

A tray utility for **Claude Code** with a working macOS app today and shared core modules that are ready to support future Windows/Linux frontends.

<br>

| State | Dot | Meaning |
|-------|-----|---------|
| **Working** | 🟢 spinning arc | Claude Code is executing a tool or shell command |
| **Idle**    | 🟡 solid        | Claude Code is running, waiting for your input |
| **Stopped** | 🔴 solid        | Claude Code process not found |

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Gowtham-M-k/claude-code-tray/main/agentwatch/install.sh | sh
```

> Requires **macOS 12 Monterey or later** and **Python 3.9+**

The installer will:
- Install Python dependencies (`rumps`, `psutil`, `pyobjc-framework-Quartz`, `tomli`)
- Copy files to `~/.agentwatch/`
- Register a **LaunchAgent** so it auto-starts at login

---

## What It Shows

The tray icon still reflects live agent state:

1. Finds the top-level Claude Code process
2. Checks for child processes and parent CPU activity
3. Shows `working`, `idle`, or `stopped`
4. Holds `working` briefly so short subprocess gaps do not flicker

The tray menu now also scans `~/.claude/projects/**/*.jsonl` every 2 seconds and shows:

- Active Claude agents
- A compact top-line summary with status, agent count, token totals, and cache rate
- Total input tokens
- Total output tokens
- Cache hit rate
- Cost today
- Cost all-time
- Last tool used
- JSONL file count

If no Claude JSONL logs exist yet, metric rows show `No data yet`.

## Auto Update

AgentWatch now checks for updates automatically once per day by default.

- It compares the local `VERSION` file with the remote GitHub raw `VERSION`
- If a newer version is available, it downloads the app files into the installed `~/.agentwatch/` directory
- After a successful update, it restarts itself automatically
- You can also trigger this manually from the tray menu with `Check for updates now`

## Phase 4 Foundation

The repo is now split into reusable pieces instead of one large script:

- `agentwatch_core.py` for config, process scanning, JSONL parsing, formatting
- `agentwatch_alerts.py` for alert rules and thresholds
- `agentwatch_macos.py` for the macOS tray UI
- `agentwatch.py` as the platform-aware entrypoint
- `agentwatch_mac.py` as a backward-compatible macOS launcher

That keeps macOS working now while making the next Windows/Linux UI work incremental instead of another rewrite.

## Alerts And Config

Phase 3 adds native notifications for:

- `working -> idle` task completion, including the session slug in the title and a 2-line preview of the latest user request
- unexpected stop after `working` or `idle`
- daily budget exceeded

AgentWatch reads optional config from `~/.agentwatch.toml`.

```toml
poll_interval = 1.0
metrics_interval = 2.0
working_hold_sec = 4.0

[updates]
enabled = true
check_interval_sec = 86400
repo_raw = "https://raw.githubusercontent.com/Gowtham-M-k/claude-code-tray/main/agentwatch"

[alerts]
task_complete = true
agent_stopped = true
daily_budget = true
sound = true
daily_budget_usd = 5.0
```

If the file does not exist, AgentWatch uses these defaults automatically.

---

## Files

```
agentwatch/
├── agentwatch.py       — platform-aware entrypoint
├── agentwatch_mac.py   — macOS compatibility launcher
├── agentwatch_macos.py — macOS tray UI
├── agentwatch_core.py  — shared config, process, and JSONL logic
├── agentwatch_alerts.py — alert rules
├── agentwatch_updater.py — remote version check + file updater
├── VERSION            — local app version
├── agentwatch.example.toml — sample config
├── install.sh          — one-line installer
└── claude-color.svg    — Claude icon
```

---

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.agentwatch.plist
rm -rf ~/.agentwatch ~/Library/LaunchAgents/com.agentwatch.plist
```

---

## Troubleshooting

**Always shows Stopped even when Claude Code is running**

Check what processes are visible:
```bash
python3 -c "import psutil; [print(p.name(), p.cmdline()) for p in psutil.process_iter() if 'claude' in ' '.join(p.cmdline()).lower()]"
```

**View logs**
```bash
tail -f ~/.agentwatch.log
```

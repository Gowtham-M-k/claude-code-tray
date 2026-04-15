# AgentWatch

A macOS menu bar indicator that shows whether **Claude Code** is actively working or idle — at a glance, without switching windows.

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
- Install Python dependencies (`rumps`, `psutil`)
- Copy files to `~/.agentwatch/`
- Register a **LaunchAgent** so it auto-starts at login

---

## How It Works

AgentWatch polls running processes every second:

1. Finds any process whose name or command line contains `claude`
2. Checks for **child processes** — Claude Code spawns shells when running tools → **Working**
3. If no children, samples **CPU usage** — high CPU → **Working**
4. Process found but nothing active → **Idle**
5. No process found → **Stopped**

Status changes are **debounced** (3 consecutive polls must agree) to prevent flickering.

---

## Files

```
agentwatch/
├── install.sh          — one-line installer
├── agentwatch_mac.py   — menu bar app
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

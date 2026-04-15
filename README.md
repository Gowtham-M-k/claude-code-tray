# Claude Code Status Tray

A lightweight, cross-platform system-tray indicator that shows whether
**Claude Code** is actively working or idle — at a glance, without switching
windows.

```
🟢 Working  — Claude Code is executing a tool / shell command
🟡 Idle     — Claude Code is running, waiting for your input
⚫ Stopped  — Claude Code process not found
```

---

## How It Works

The tray app polls running processes every 2 seconds using **psutil**:

1. It looks for any process whose **name** or **command line** contains `claude`.
2. If a match is found it then checks for **child processes** — Claude Code
   spawns shells, Python interpreters, etc. when executing tools.  
   Child processes present → **Working**.
3. If no children, CPU usage is sampled.  High CPU → **Working**.
4. Process found but nothing active → **Idle**.
5. No matching process → **Stopped**.

This means the indicator reliably lights up green when Claude Code is running
a bash command, reading a file, or calling an API — and goes amber the moment
it stops and waits for you.

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python      | 3.10+   |
| pillow      | ≥ 10    |
| psutil      | ≥ 5.9   |
| pystray     | ≥ 0.19  |

---

## Quick Start

### macOS

```bash
chmod +x setup_mac.sh
./setup_mac.sh
```

This installs the Python packages and registers a **LaunchAgent** that
auto-starts the tray at login.

> **Tip:** macOS may ask you to grant Accessibility / System Events permissions
> the first time — click *Allow* in the dialog.

### Linux

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

Installs packages and registers an **XDG autostart** desktop entry.

**GNOME 40+ users:** the system tray was removed from GNOME Shell.
Install the [AppIndicator/KStatusNotifierItem](https://extensions.gnome.org/extension/615/appindicator-support/)
extension to restore it.

**KDE / XFCE / i3 / Sway:** works out of the box.

### Windows

Double-click **`setup_windows.bat`** (or run from a terminal).

It installs the Python packages and adds a shortcut to your
**Startup** folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)
so the tray starts with Windows.

---

## Manual Run (any platform)

```bash
pip install -r requirements.txt
python claude_tray.py
```

---

## Customisation

All tunable values are at the top of `claude_tray.py`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `POLL_INTERVAL` | `2.0` s | How often to check process status |
| `CPU_THRESHOLD` | `4.0` % | CPU % that counts as "working" |
| `CPU_SAMPLE_TIME` | `0.4` s | How long to sample CPU |
| `PROC_NAME_HINTS` | `["claude"]` | Process name substrings to match |
| `CMDLINE_HINTS` | `["claude", ...]` | Command-line substrings to match |

---

## Uninstall

### macOS
```bash
launchctl unload ~/Library/LaunchAgents/com.claude.tray.plist
rm ~/Library/LaunchAgents/com.claude.tray.plist
```

### Linux
```bash
rm ~/.config/autostart/claude-tray.desktop
```
Then kill any running `claude_tray.py` process.

### Windows
Delete `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ClaudeCodeTray.lnk`
and end the `python` process in Task Manager.

---

## Troubleshooting

**Icon doesn't appear on macOS Big Sur+**  
Ensure your Python is *not* from a Homebrew arm64/x86 mismatch.
Use `python3 --version` and `which python3` to confirm.

**Always shows "Stopped" even when Claude Code is running**  
Claude Code may be installed under a different name. Run:
```bash
python3 -c "import psutil; [print(p.name(), p.cmdline()) for p in psutil.process_iter() if 'claude' in ' '.join(p.cmdline()).lower()]"
```
Then add the matching string to `CMDLINE_HINTS` in `claude_tray.py`.

**High CPU from the tray itself**  
Increase `POLL_INTERVAL` to `5.0` or `10.0` seconds.

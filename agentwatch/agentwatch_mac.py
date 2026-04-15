#!/usr/bin/env python3
"""
Backward-compatible macOS entrypoint for AgentWatch.
"""

import sys

# Hide from Dock and App Switcher — must happen before rumps/AppKit initialises
try:
    import AppKit

    AppKit.NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
except Exception:
    pass

_MISSING = []
try:
    import rumps  # noqa: F401
except ImportError:
    _MISSING.append("rumps")
try:
    import psutil  # noqa: F401
except ImportError:
    _MISSING.append("psutil")

if _MISSING:
    print(f"[AgentWatch] Missing: pip install {' '.join(_MISSING)}")
    sys.exit(1)

from agentwatch_macos import main


if __name__ == "__main__":
    main()

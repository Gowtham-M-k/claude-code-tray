#!/usr/bin/env python3

import sys


APP_ENTRYPOINT = "agentwatch.py"


def main():
    if sys.platform == "darwin":
        from agentwatch_mac import main as mac_main

        mac_main()
        return

    print(
        "AgentWatch tray UI is currently implemented for macOS in this repo. "
        "The shared core modules are now ready for future Windows/Linux frontends.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()

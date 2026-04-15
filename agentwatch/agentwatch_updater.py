import os
import tempfile
import urllib.error
import urllib.request
from typing import Optional

from agentwatch_core import REMOTE_FILES, VERSION_FILENAME


class UpdateResult:
    def __init__(self, updated: bool, version: Optional[str] = None, error: Optional[str] = None):
        self.updated = updated
        self.version = version
        self.error = error


def fetch_text(url: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def check_remote_version(repo_raw: str, timeout: float = 10.0) -> Optional[str]:
    try:
        return fetch_text(f"{repo_raw}/{VERSION_FILENAME}", timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def apply_update(install_dir: str, repo_raw: str, current_version: str) -> UpdateResult:
    remote_version = check_remote_version(repo_raw)
    if not remote_version or remote_version == current_version:
        return UpdateResult(updated=False, version=remote_version)

    temp_dir = tempfile.mkdtemp(prefix="agentwatch-update-")
    downloaded_paths = []
    try:
        for filename in REMOTE_FILES:
            url = f"{repo_raw}/{filename}"
            target_path = os.path.join(temp_dir, filename)
            with urllib.request.urlopen(url, timeout=15.0) as response, open(
                target_path, "wb"
            ) as handle:
                handle.write(response.read())
            downloaded_paths.append((filename, target_path))

        for filename, temp_path in downloaded_paths:
            final_path = os.path.join(install_dir, filename)
            os.replace(temp_path, final_path)

        for executable in ("agentwatch.py", "agentwatch_mac.py"):
            path = os.path.join(install_dir, executable)
            if os.path.exists(path):
                os.chmod(path, 0o755)
        return UpdateResult(updated=True, version=remote_version)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return UpdateResult(updated=False, version=remote_version, error=str(exc))
    finally:
        for _, temp_path in downloaded_paths:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass

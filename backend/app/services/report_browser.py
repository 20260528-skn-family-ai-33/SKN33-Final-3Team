"""Use the same headless browser selection for startup checks and PDF rendering."""
from pathlib import Path
import subprocess
import sys


def launch_chromium(runtime):
    from playwright.sync_api import Error
    try:
        return runtime.chromium.launch(headless=True, timeout=30000)
    except Error as error:
        # Preserve deployments which installed only full Chromium. Other launch
        # failures are propagated rather than hidden by a different executable.
        if "Executable doesn't exist" not in str(error) or not Path(runtime.chromium.executable_path).is_file():
            raise
        return runtime.chromium.launch(headless=True, executable_path=runtime.chromium.executable_path, timeout=30000)


def ensure_chromium():
    from playwright.sync_api import sync_playwright, Error
    with sync_playwright() as runtime:
        try:
            with launch_chromium(runtime):
                return
        except Error as error:
            if "Executable doesn't exist" not in str(error):
                raise
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
        with launch_chromium(runtime):
            pass

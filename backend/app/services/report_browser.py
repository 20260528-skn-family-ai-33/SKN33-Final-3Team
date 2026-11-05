"""Install the worker's browser once; ordinary restarts only check the files."""
from pathlib import Path
import subprocess
import sys


def ensure_chromium():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        executable=Path(runtime.chromium.executable_path)
    if not executable.is_file():
        subprocess.run([sys.executable,'-m','playwright','install','chromium'],check=True)
    # Use the installed full Chromium for both the check and PDF generation.
    # This avoids relying on a separate headless-shell download.

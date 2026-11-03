import unittest
from unittest.mock import MagicMock,patch
from backend.app.services.report_browser import ensure_chromium
class ReportBrowserSetupTest(unittest.TestCase):
 def test_installed_browser_does_not_run_installer(self):
  runtime=MagicMock();runtime.__enter__.return_value.chromium.executable_path='browser.exe'
  with patch('playwright.sync_api.sync_playwright',return_value=runtime),patch('pathlib.Path.is_file',return_value=True),patch('subprocess.run') as install:
   ensure_chromium();install.assert_not_called()
 def test_missing_browser_installs_with_same_python(self):
  import sys
  runtime=MagicMock();runtime.__enter__.return_value.chromium.executable_path='browser.exe'
  with patch('playwright.sync_api.sync_playwright',return_value=runtime),patch('pathlib.Path.is_file',return_value=False),patch('subprocess.run') as install:
   ensure_chromium();install.assert_called_once_with([sys.executable,'-m','playwright','install','chromium'],check=True)

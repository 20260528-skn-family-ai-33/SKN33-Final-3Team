import sys
import unittest
from unittest.mock import MagicMock, patch
from playwright.sync_api import Error
from backend.app.services.report_browser import ensure_chromium, launch_chromium


class ReportBrowserSetupTest(unittest.TestCase):
    def runtime(self):
        manager = MagicMock()
        runtime = manager.__enter__.return_value
        runtime.chromium.executable_path = 'browser.exe'
        return manager, runtime

    def test_installed_headless_browser_does_not_run_installer(self):
        manager, runtime = self.runtime()
        with patch('playwright.sync_api.sync_playwright', return_value=manager), patch('subprocess.run') as install:
            ensure_chromium()
        runtime.chromium.launch.assert_called_once_with(headless=True, timeout=30000)
        install.assert_not_called()

    def test_full_only_installation_uses_existing_browser(self):
        _, runtime = self.runtime()
        runtime.chromium.launch.side_effect = [Error("Executable doesn't exist at headless-shell.exe"), MagicMock()]
        with patch('pathlib.Path.is_file', return_value=True):
            launch_chromium(runtime)
        self.assertEqual(runtime.chromium.launch.call_count, 2)
        runtime.chromium.launch.assert_called_with(headless=True, executable_path='browser.exe', timeout=30000)

    def test_missing_browser_installs_with_same_python_and_checks_launch(self):
        manager, runtime = self.runtime()
        runtime.chromium.launch.side_effect = [Error("Executable doesn't exist at headless-shell.exe"), MagicMock()]
        with patch('playwright.sync_api.sync_playwright', return_value=manager), \
                patch('pathlib.Path.is_file', return_value=False), patch('subprocess.run') as install:
            ensure_chromium()
        install.assert_called_once_with([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
        self.assertEqual(runtime.chromium.launch.call_count, 2)

    def test_launch_failure_does_not_install_or_switch_browsers(self):
        manager, runtime = self.runtime()
        runtime.chromium.launch.side_effect = Error('spawn UNKNOWN')
        with patch('playwright.sync_api.sync_playwright', return_value=manager), patch('subprocess.run') as install:
            with self.assertRaisesRegex(Error, 'spawn UNKNOWN'):
                ensure_chromium()
        install.assert_not_called()
        self.assertEqual(runtime.chromium.launch.call_count, 1)

    def test_still_missing_after_install_is_reported(self):
        manager, runtime = self.runtime()
        runtime.chromium.launch.side_effect = Error("Executable doesn't exist")
        with patch('playwright.sync_api.sync_playwright', return_value=manager), \
                patch('pathlib.Path.is_file', return_value=False), patch('subprocess.run') as install:
            with self.assertRaisesRegex(Error, "Executable doesn't exist"):
                ensure_chromium()
        self.assertEqual(install.call_count, 1)

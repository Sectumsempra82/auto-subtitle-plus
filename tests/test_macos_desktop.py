import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from auto_subtitle_plus.desktop.state import QueueItem, StateStore
from tools.build_macos import build_app


class MacStateTests(unittest.TestCase):
    def test_legacy_state_migrates_without_starting_work_or_deleting_old_state(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("auto_subtitle_plus.desktop.state.sys.platform", "darwin"), \
                mock.patch("auto_subtitle_plus.desktop.state.Path.home", return_value=Path(tmp)):
            old = Path(tmp) / ".cache/AutoSubtitlePlus/desktop/state.json"
            StateStore(old).save({"model": "tiny"}, [QueueItem("/tmp/clip.wav", status="running")])
            store = StateStore()
            self.assertEqual(store.path, Path(tmp) / "Library/Application Support/AutoSubtitlePlus/desktop/state.json")
            settings, items = store.load()
            self.assertEqual(items[0].status, "interrupted")
            store.save(settings, items)
            self.assertTrue(old.exists())
            StateStore(old).save({"model": "large-v3"}, [])
            self.assertEqual(store.load()[0]["model"], "tiny")

    def test_explicit_storage_override_wins_on_mac(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/tmp/asp-data"}), \
                mock.patch("auto_subtitle_plus.desktop.state.sys.platform", "darwin"):
            store = StateStore()
            self.assertEqual(store.path, Path("/tmp/asp-data/AutoSubtitlePlus/desktop/state.json"))
            self.assertIsNone(store.legacy_path)


class MacLauncherTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and importlib.util.find_spec("PySide6"), "Native launcher requires macOS and desktop dependencies")
    def test_launcher_preserves_venv_and_quotes_paths(self):
        with tempfile.TemporaryDirectory(prefix="asp ' space ") as tmp:
            root = Path(tmp)
            python = Path(sys.executable)
            app = build_app(root, python, root / "dist")
            with (app / "Contents/Info.plist").open("rb") as stream:
                metadata = plistlib.load(stream)
            launcher = app / "Contents/MacOS" / metadata["CFBundleExecutable"]
            self.assertTrue(os.access(launcher, os.X_OK))
            binary = launcher.read_bytes()
            self.assertIn(str(python).encode(), binary)
            self.assertIn(str(root).encode(), binary)
            self.assertIn("Mach-O", subprocess.run(["file", str(launcher)], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(metadata["CFBundleDocumentTypes"][0]["LSHandlerRank"], "Alternate")
            package = root / "auto_subtitle_plus"
            desktop = package / "desktop"
            desktop.mkdir(parents=True)
            (package / "__init__.py").touch()
            (desktop / "__init__.py").touch()
            (desktop / "__main__.py").write_text(
                "import json, subprocess, sys\n"
                "child = subprocess.run([sys.executable, '-c', 'print(42)'], capture_output=True, text=True, check=True)\n"
                "print(json.dumps({'python': sys.executable, 'args': sys.argv[1:], 'child': child.stdout.strip()}))\n"
            )
            result = subprocess.run([str(launcher), "a file with spaces.wav"], capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(json.loads(result.stdout), {"python": str(python), "args": ["a file with spaces.wav"], "child": "42"})


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "Optional GUI dependencies are not installed")
class MacDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_native_tabs_and_colors_survive_live_appearance_changes(self):
        if sys.platform != "darwin" or self.app.platformName() != "cocoa":
            self.skipTest("Run with QT_QPA_PLATFORM=cocoa to verify native Mac rendering")
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontDatabase, QPalette
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionTab
        from auto_subtitle_plus.desktop.window import MainWindow, apply_theme
        original_font = self.app.font()
        original_scheme = self.app.styleHints().colorScheme()
        with tempfile.TemporaryDirectory() as tmp:
            self.app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
            apply_theme(self.app)
            window = MainWindow(store=StateStore(Path(tmp) / "state.json"), monitor=False)
            window.show()
            try:
                for scheme in (Qt.ColorScheme.Dark, Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
                    self.app.styleHints().setColorScheme(scheme)
                    QTest.qWait(80)
                    for width, height in ((960, 660), (1100, 780), (1440, 900)):
                        window.resize(width, height)
                        for index in range(window.settings.tabs.count()):
                            window.settings.tabs.setCurrentIndex(index)
                            self.app.processEvents()
                            bar = window.settings.tabs.tabBar()
                            option = QStyleOptionTab()
                            bar.initStyleOption(option, index)
                            text_rect = bar.style().subElementRect(QStyle.SubElement.SE_TabBarTabText, option, bar)
                            self.assertGreaterEqual(text_rect.width() - bar.fontMetrics().horizontalAdvance(bar.tabText(index)), 8)
                            self.assertTrue(bar.rect().contains(bar.tabRect(index)))
                            scroll = window.settings.tabs.currentWidget()
                            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
                            self.assertEqual(scroll.widget().palette().color(QPalette.ColorRole.Window), self.app.palette().color(QPalette.ColorRole.Window))
                        self.assertEqual(window.summary.palette().color(QPalette.ColorRole.WindowText).name(), self.app.palette().color(QPalette.ColorRole.WindowText).name())
                        self.assertEqual(window.activity.palette().color(QPalette.ColorRole.Base), self.app.palette().color(QPalette.ColorRole.Base))
            finally:
                window.close()
                window.deleteLater()
                self.app.styleHints().setColorScheme(original_scheme)
                self.app.setFont(original_font)

    def test_mac_translation_defaults_and_unsupported_hardware_validation(self):
        from auto_subtitle_plus.desktop.settings import SettingsPanel
        with mock.patch("auto_subtitle_plus.desktop.settings.sys.platform", "darwin"):
            panel = SettingsPanel()
            try:
                panel.restore({"translate_enabled": True, "translate_to": "fr", "language": "en"})
                self.assertEqual(panel.translation_model.currentText(), "m2m100-418m")
                self.assertEqual(panel.translation_engine.count(), 1)
                options = panel.options()
                panel.validate_platform(options)
                with self.assertRaisesRegex(ValueError, "CUDA"):
                    panel.validate_platform({**options, "device": "cuda"})
                with self.assertRaisesRegex(ValueError, "Windows-only"):
                    panel.validate_platform({**options, "translation_model": "hy-mt2-1.8b-q8"})
                with self.assertRaisesRegex(ValueError, "local translation only"):
                    panel.validate_platform({**options, "translation_engine": "google"})
            finally:
                panel.deleteLater()

    def test_finder_events_wait_for_window_then_enqueue_and_activate(self):
        from PySide6.QtGui import QFileOpenEvent
        from auto_subtitle_plus.desktop.window import DesktopApplication
        host = SimpleNamespace(window=None, pending_files=[])
        self.assertTrue(DesktopApplication.event(host, QFileOpenEvent("/tmp/clip.wav")))
        self.assertEqual(host.pending_files, ["/tmp/clip.wav"])
        host.window = mock.Mock()
        DesktopApplication.event(host, QFileOpenEvent("/tmp/second.wav"))
        host.window.add_paths.assert_called_once_with(["/tmp/second.wav"])
        host.window.activateWindow.assert_called_once()


if __name__ == "__main__":
    unittest.main()

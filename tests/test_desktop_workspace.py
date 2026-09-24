"""The shared desktop workspace, which macOS and Windows both launch."""
import importlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from auto_subtitle_plus.desktop.state import QueueItem, StateStore


class WorkspaceDispatchTests(unittest.TestCase):
    def test_macos_and_windows_launch_the_workspace_and_others_keep_the_classic_window(self):
        import auto_subtitle_plus.desktop as desktop

        for platform, module in (("darwin", "workspace"), ("win32", "workspace"), ("linux", "window")):
            with self.subTest(platform=platform):
                launch = mock.Mock(return_value=0)
                shell = mock.Mock(launch=launch)
                with mock.patch.object(desktop.sys, "platform", platform):
                    with mock.patch.dict(sys.modules, {f"auto_subtitle_plus.desktop.{module}": shell}):
                        self.assertEqual(desktop.main(), 0)
                launch.assert_called_once_with()


class WorkspaceWordingTests(unittest.TestCase):
    def test_platform_wording_names_the_machine_and_its_own_shortcut(self):
        import auto_subtitle_plus.desktop.workspace.wording as wording

        for platform, device, label in (("darwin", "Mac", "Command-K"), ("win32", "PC", "Ctrl+K")):
            with self.subTest(platform=platform):
                with mock.patch.object(wording.sys, "platform", platform):
                    importlib.reload(wording)
                    self.assertEqual(wording.DEVICE, device)
                    self.assertEqual(wording.SEARCH_SHORTCUT_LABEL, label)
                    # Qt renders the portable sequence as Command on macOS.
                    self.assertEqual(wording.SEARCH_SHORTCUT, "Ctrl+K")

    def test_compute_type_hint_only_claims_unsupported_hardware_on_a_mac(self):
        import auto_subtitle_plus.desktop.workspace.wording as wording

        with mock.patch.object(wording.sys, "platform", "win32"):
            importlib.reload(wording)
            self.assertNotIn("Mac", wording.COMPUTE_TYPE_HINT)
            self.assertIn("precision", wording.COMPUTE_TYPE_HINT)
        with mock.patch.object(wording.sys, "platform", "darwin"):
            importlib.reload(wording)
            self.assertEqual(wording.COMPUTE_TYPE_HINT, "Not supported on this Mac.")

    def tearDown(self):
        import auto_subtitle_plus.desktop.workspace.wording as wording
        importlib.reload(wording)


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "Optional GUI dependencies are not installed")
class WorkspaceShellSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_shell_navigation_and_settings_preserve_all_sections(self):
        from auto_subtitle_plus.desktop.workspace.shell import WorkspaceMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            window = WorkspaceMainWindow(store=StateStore(Path(tmp) / "state.json"), monitor=False)
            try:
                self.assertEqual(window.pages.count(), 4)
                self.assertEqual(window.pages.currentIndex(), 0)
                self.assertIn("Queue", window.nav_buttons)
                self.assertIn("Speech", window.nav_buttons)
                self.assertIn("Translate", window.nav_buttons)
                self.assertIn("History", window.nav_buttons)
                self.assertIn("Settings", window.nav_buttons)
                self.assertIn("Help", window.nav_buttons)
                window._select_page("History")
                self.assertEqual(window.pages.currentWidget(), window.processing)

                expected_tabs = ("Speech", "Translate", "Layout", "Files", "System")
                self.assertEqual(
                    tuple(window.settings.tabs.tabText(index) for index in range(window.settings.tabs.count())),
                    expected_tabs,
                )
                for index, tab in enumerate(expected_tabs):
                    window._select_page(tab if tab in ("Speech", "Translate") else "Settings")
                    if tab in ("Speech", "Translate"):
                        self.assertEqual(window.forms.pages.currentWidget(), getattr(window.forms, tab.lower()))
                    else:
                        window.forms.show_settings(tab)
                        self.assertEqual(window.forms.settings_stack.currentIndex(), index - 2)
                    self.assertEqual(window.pages.currentIndex(), 1)

                preserved = {
                    "backend": "faster",
                    "model": "medium",
                    "language": "es",
                    "translate_enabled": True,
                    "translate_to": "it",
                    "translation_route": "via-en",
                    "context": "academic lecture",
                    "glossary_text": '{"machine learning": "apprendimento automatico"}',
                    "max_cps": 17.5,
                    "subtitle_format": "VTT",
                    "output_txt": True,
                    "offline": True,
                    "verbose": True,
                }
                window.settings.restore(preserved)
                snapshot = window.settings.snapshot()
                options = window.settings.options()
                for key, value in preserved.items():
                    self.assertEqual(snapshot[key], value, key)
                self.assertEqual(options["translation_route"], "via-en")
                self.assertEqual(options["context"], "academic lecture")
                self.assertEqual(options["glossary"], {"machine learning": "apprendimento automatico"})
                self.assertEqual(options["max_cps"], 17.5)
                self.assertEqual(options["subtitle_format"], "vtt")
                self.assertTrue(options["output_txt"])
                self.assertTrue(options["offline"])
                self.assertTrue(options["verbose"])
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_search_uses_a_window_shortcut_and_leaves_the_menu_bar_alone(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        from auto_subtitle_plus.desktop.workspace.shell import WorkspaceMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            window = WorkspaceMainWindow(store=StateStore(Path(tmp) / "state.json"), monitor=False)
            try:
                keys = [shortcut.key() for shortcut in window.findChildren(QShortcut)]
                self.assertIn(QKeySequence("Ctrl+K"), keys)
                # A bare "Search Settings" entry would sit beside File on Windows.
                self.assertEqual([action.text() for action in window.menuBar().actions()],
                                 ["File", "Queue", "Help"])
                window._select_page("Speech")
                window._focus_search()
                # The window is never activated offscreen, so check the
                # in-window focus widget rather than keyboard focus.
                self.assertIs(window.focusWidget(), window.search)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_dropdown_popups_stay_readable_on_the_selected_entry(self):
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QListView, QStyledItemDelegate
        from auto_subtitle_plus.desktop.workspace.shell import WorkspaceMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            window = WorkspaceMainWindow(store=StateStore(Path(tmp) / "state.json"), monitor=False)
            try:
                settings = window.settings
                combos = ("backend", "model", "language", "device", "compute_type",
                          "translate_to", "translation_device", "subtitle_format")
                for name in combos:
                    combo = getattr(settings, name)
                    with self.subTest(combo=name):
                        # Native popups paint the current entry themselves and
                        # ignore the workspace colors, hiding its label.
                        self.assertIsInstance(combo.view(), QListView)
                        self.assertIs(type(combo.itemDelegate()), QStyledItemDelegate)

                device = settings.device
                device.setCurrentIndex(1)
                device.showPopup()
                self.app.processEvents()
                view = device.view()
                view.resize(view.sizeHint())
                self.app.processEvents()
                pixmap = QPixmap(view.size())
                view.render(pixmap)
                image = pixmap.toImage()
                for row in range(device.count()):
                    rect = view.visualRect(view.model().index(row, 0))
                    ink = sum(
                        1
                        for y in range(rect.top(), min(rect.bottom(), image.height() - 1))
                        for x in range(rect.left(), min(rect.right(), image.width() - 1))
                        if image.pixelColor(x, y).lightness() < 140
                    )
                    self.assertGreater(ink, 0, device.itemText(row))
                device.hidePopup()
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_processing_review_uses_saved_outputs_without_loading_media(self):
        from auto_subtitle_plus.desktop.workspace.shell import WorkspaceMainWindow

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "clip.txt"
            transcript.write_text("Translated text", encoding="utf-8")
            original = root / "clip.source.es.txt"
            original.write_text("Original text", encoding="utf-8")
            subtitles = root / "clip.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nSubtitle text\n", encoding="utf-8")
            window = WorkspaceMainWindow(store=StateStore(root / "state.json"), monitor=False)
            try:
                item = QueueItem(str(root / "clip.wav"), status="completed", outputs=[str(transcript), str(original), str(subtitles)])
                window.model.replace([item])
                window.table.selectRow(0)
                window._item_translation[item.id] = True
                window._select_page("Processing")
                self.assertEqual([window.processing.preview.tabText(index) for index in range(5)],
                                 ["Transcript", "Translation", "Subtitles", "Files", "Activity"])
                self.assertIn("Original text", window.processing.text_preview.toPlainText())
                self.assertIn("Translated text", window.processing.translation_preview.toPlainText())
                self.assertIn("Subtitle text", window.processing.subtitle_preview.toPlainText())
                self.assertEqual(window.processing.subtitle_rows.rowCount(), 1)
                self.assertEqual(window.processing.subtitle_rows.item(0, 0).text(), "00:00:00,000")
                self.assertEqual(window.processing.subtitle_rows.item(0, 1).text(), "Subtitle text")
                self.assertEqual(window.outputs.count(), 3)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
import importlib.util
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("Optional GUI dependencies are not installed")

from PySide6.QtGui import QFont
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QScrollArea

from auto_subtitle_plus.desktop.settings import SettingsPanel


class SettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(QFont("Arial", 8))

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_defaults_return_job_options_without_paths_or_legacy_noops(self):
        panel = SettingsPanel()
        options = panel.options()

        self.assertNotIn("paths", options)
        self.assertNotIn("path", options)
        self.assertNotIn("batch_size", options)
        self.assertNotIn("max_workers", options)
        self.assertEqual(options["backend"], "stable")
        self.assertEqual(options["model"], "small")
        self.assertIsNone(options["device"])
        self.assertEqual(options["compute_type"], "auto")
        self.assertEqual(options["inference_batch_size"], 1)
        self.assertFalse(options["vad"])
        self.assertIsNone(options["translate_to"])
        self.assertFalse(options["bilingual"])
        self.assertEqual(options["translation_route"], "direct")
        self.assertEqual(options["context"], "")
        self.assertIsNone(options["glossary"])
        self.assertIsNone(options["output_dir"])
        self.assertTrue(options["output_srt"])
        self.assertFalse(options["overwrite"])

    def test_restore_preserves_gui_toggles_and_translation_disabled_normalizes_options(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "translate_enabled": False,
                "translate_to": "fr",
                "bilingual": True,
                "translation_route": "via-en",
                "output_source_subtitles": True,
                "output_intermediate_subtitles": True,
                "context": "tone notes",
                "glossary": {"cat": "chat"},
            }
        )

        snapshot = panel.snapshot()
        options = panel.options()

        self.assertEqual(snapshot["translate_to"], "fr")
        self.assertTrue(snapshot["bilingual"])
        self.assertEqual(snapshot["translation_route"], "via-en")
        self.assertEqual(snapshot["context"], "tone notes")
        self.assertIsNone(options["translate_to"])
        self.assertFalse(options["bilingual"])
        self.assertEqual(options["translation_route"], "direct")
        self.assertFalse(options["output_source_subtitles"])
        self.assertFalse(options["output_intermediate_subtitles"])
        self.assertEqual(options["context"], "")
        self.assertIsNone(options["glossary"])

    def test_stable_backend_disables_and_normalizes_faster_only_options(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "backend": "stable",
                "compute_type": "float16",
                "inference_batch_size": 8,
                "vad": True,
            }
        )

        self.assertFalse(panel.compute_type.isEnabled())
        self.assertFalse(panel.inference_batch_size.isEnabled())
        self.assertFalse(panel.vad.isEnabled())
        options = panel.options()
        self.assertEqual(options["compute_type"], "auto")
        self.assertEqual(options["inference_batch_size"], 1)
        self.assertFalse(options["vad"])

    def test_invalid_faster_batch_without_vad_rejects_options(self):
        panel = SettingsPanel()
        panel.restore({"backend": "faster", "inference_batch_size": 4, "vad": False})

        with self.assertRaisesRegex(ValueError, "requires VAD"):
            panel.options()

    def test_invalid_translation_model_is_preserved_and_rejected(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "translate_enabled": True,
                "language": "en",
                "translate_to": "fr",
                "translation_engine": "local",
                "translation_route": "direct",
                "translation_model": "opus-de-it",
            }
        )

        self.assertEqual(panel.snapshot()["translation_model"], "opus-de-it")
        self.assertIn("Invalid model", panel.model_info.text())
        with self.assertRaisesRegex(ValueError, "translation model"):
            panel.options()

    def test_glossary_json_maps_to_dict_and_invalid_json_rejects(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "translate_enabled": True,
                "language": "en",
                "translate_to": "fr",
                "translation_model": "hy-mt2-1.8b-q8",
                "glossary_text": '{"AI": "IA"}',
            }
        )

        self.assertEqual(panel.options()["glossary"], {"AI": "IA"})
        panel.glossary.setPlainText("[1, 2]")
        with self.assertRaisesRegex(ValueError, "Glossary JSON"):
            panel.options()

        panel.glossary.setPlainText('{"AI": {"nested": "bad"}}')
        with self.assertRaisesRegex(ValueError, "terms and values"):
            panel.options()

    def test_model_info_reports_cheap_cache_file_presence_without_checksum_claim(self):
        from auto_subtitle_plus.desktop import settings as settings_module

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            panel = SettingsPanel()
            panel.restore(
                {
                    "translate_enabled": True,
                    "language": "en",
                    "translate_to": "fr",
                    "translation_model": "hy-mt2-1.8b-q8",
                }
            )
            with mock.patch.object(settings_module, "source_dir", return_value=cache_dir):
                panel._refresh_translation_models("hy-mt2-1.8b-q8")
                self.assertIn("Download required", panel.model_info.text())

                spec = next(spec for spec in settings_module.list_models(None, None) if spec.id == "hy-mt2-1.8b-q8")
                for item in spec.files:
                    path = cache_dir / item.name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("", encoding="utf-8")

                panel._refresh_translation_models("hy-mt2-1.8b-q8")
                self.assertIn("Cached files (verified at start)", panel.model_info.text())
                self.assertNotIn("installed", panel.model_info.text().lower())

    def test_disabled_translation_does_not_parse_invalid_glossary(self):
        panel = SettingsPanel()
        panel.restore({"translate_enabled": False, "glossary_text": "[not json"})

        options = panel.options()

        self.assertIsNone(options["translate_to"])
        self.assertIsNone(options["glossary"])

    def test_layout_float_values_restore_without_truncation(self):
        panel = SettingsPanel()
        panel.restore({"max_cps": 17.5, "min_duration": 1.5, "max_duration": 7.5})

        snapshot = panel.snapshot()
        options = panel.options()

        self.assertEqual(snapshot["max_cps"], 17.5)
        self.assertEqual(snapshot["min_duration"], 1.5)
        self.assertEqual(snapshot["max_duration"], 7.5)
        self.assertEqual(options["max_cps"], 17.5)
        self.assertEqual(options["min_duration"], 1.5)
        self.assertEqual(options["max_duration"], 7.5)

    def test_files_and_system_options_map_to_cli_destinations(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "output_location": "folder",
                "output_dir": "C:/out",
                "output_video": True,
                "video_mode": "MKV soft",
                "subtitle_format": "VTT",
                "output_txt": True,
                "output_audio": True,
                "overwrite": True,
                "offline": True,
                "retry_translation": True,
                "verbose": True,
                "translation_cache_dir": "C:/cache",
                "extract_workers": 3,
            }
        )

        options = panel.options()
        self.assertEqual(options["output_dir"], "C:/out")
        self.assertTrue(options["output_video"])
        self.assertTrue(options["output_mkv"])
        self.assertEqual(options["subtitle_format"], "vtt")
        self.assertTrue(options["output_txt"])
        self.assertTrue(options["output_audio"])
        self.assertTrue(options["overwrite"])
        self.assertTrue(options["offline"])
        self.assertTrue(options["retry_translation"])
        self.assertTrue(options["verbose"])
        self.assertEqual(options["translation_cache_dir"], "C:/cache")
        self.assertEqual(options["extract_workers"], 3)

    def test_clear_cache_button_emits_signal(self):
        panel = SettingsPanel()
        emitted = []
        panel.clear_cache_requested.connect(lambda: emitted.append(True))

        panel.clear_cache.click()

        self.assertEqual(emitted, [True])

    def test_narrow_render_has_no_horizontal_scroll_or_tab_overflow(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "translate_enabled": True,
                "language": "en",
                "translate_to": "fr",
                "translation_model": "a-very-long-model-name-that-must-not-resize-the-form",
                "output_location": "folder",
                "output_dir": "C:/a/really/long/output/path/that/should/not/stretch/the/form",
                "translation_cache_dir": "C:/a/really/long/cache/path/that/should/not/stretch/the/form",
            }
        )
        panel.resize(400, 550)
        panel.show()
        self.app.processEvents()

        for scroll in panel.findChildren(QScrollArea):
            self.assertFalse(scroll.horizontalScrollBar().isVisible())
            self.assertLessEqual(scroll.widget().sizeHint().width(), scroll.viewport().width())

        panel.resize(360, 550)
        self.app.processEvents()
        tab_bar = panel.tabs.tabBar()
        total_tab_width = sum(tab_bar.tabRect(index).width() for index in range(tab_bar.count()))
        self.assertEqual([tab_bar.tabText(index) for index in range(tab_bar.count())], ["Speech", "Translate", "Layout", "Files", "System"])
        self.assertLessEqual(total_tab_width, tab_bar.width())

    def test_compact_host_window_height_uses_scroll_pages(self):
        panel = SettingsPanel()
        panel.resize(400, 520)
        panel.show()
        self.app.processEvents()

        self.assertLessEqual(panel.minimumHeight(), 250)
        for scroll in panel.findChildren(QScrollArea):
            if not scroll.isVisible():
                continue
            self.assertFalse(scroll.horizontalScrollBar().isVisible())
            self.assertLessEqual(scroll.width(), panel.width())


if __name__ == "__main__":
    unittest.main()

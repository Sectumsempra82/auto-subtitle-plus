import os
import tempfile
import time
import unittest
import importlib.util
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("Optional GUI dependencies are not installed")

from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from auto_subtitle_plus.api import JobOptions
from auto_subtitle_plus.desktop.settings import SettingsPanel
from auto_subtitle_plus.desktop.window import MainWindow


def write_tiny_wav(path: Path) -> None:
    path.write_bytes(
        b"RIFF$\x00\x00\x00WAVEfmt "
        b"\x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00"
        b"data\x00\x00\x00\x00"
    )


def wait_until(predicate, timeout_ms=3000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(20)
    QApplication.processEvents()
    return predicate()


class MemoryStore:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.saved = []

    def load(self):
        return dict(self.settings), []

    def save(self, settings, items):
        self.saved.append((dict(settings), [item.path for item in items]))


class CapturingRunner:
    def __init__(self):
        self.options = []
        self.closed = False

    def run(self, options, progress=None, cancel=None):
        self.options.append(options)
        from auto_subtitle_plus.api import JobResult

        return JobResult(status="completed", outputs=())

    def close(self):
        self.closed = True


class GuiApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_settings_snapshot_restores_to_job_options_contract_without_unknown_fields(self):
        panel = SettingsPanel()
        panel.restore(
            {
                "backend": "faster",
                "model": "small",
                "language": "en",
                "device": "cpu",
                "compute_type": "int8",
                "inference_batch_size": 2,
                "vad": True,
                "word_timestamps": True,
                "enhance_consistency": True,
                "translate_enabled": True,
                "translate_to": "fr",
                "translation_engine": "local",
                "translation_route": "direct",
                "translation_model": "hy-mt2-1.8b-q8",
                "translation_device": "cpu",
                "bilingual": True,
                "context": "Keep names literal.",
                "glossary_text": '{"AI": "IA"}',
                "subtitle_layout": "preserve",
                "max_chars_per_line": 38,
                "max_lines": 2,
                "max_cps": 16,
                "min_duration": 2,
                "max_duration": 6,
                "output_location": "folder",
                "output_dir": "C:/out",
                "output_srt": True,
                "subtitle_format": "VTT",
                "output_txt": True,
                "output_audio": True,
                "output_video": True,
                "video_mode": "MKV soft",
                "output_source_subtitles": True,
                "output_intermediate_subtitles": False,
                "overwrite": True,
                "offline": True,
                "retry_translation": True,
                "verbose": True,
                "extract_workers": 2,
                "translation_cache_dir": "C:/cache",
            }
        )

        snapshot = panel.snapshot()
        options = panel.options()
        job_options = JobOptions.from_mapping({"path": "clip.wav", **options})

        self.assertTrue(snapshot["translate_enabled"])
        self.assertEqual(snapshot["glossary_text"], '{"AI": "IA"}')
        self.assertEqual(options["device"], "cpu")
        self.assertEqual(options["compute_type"], "int8")
        self.assertEqual(options["inference_batch_size"], 2)
        self.assertTrue(options["vad"])
        self.assertEqual(options["translate_to"], "fr")
        self.assertEqual(options["glossary"], {"AI": "IA"})
        self.assertEqual(options["subtitle_format"], "vtt")
        self.assertTrue(options["output_mkv"])
        self.assertTrue(options["no_adaptive_layout"])
        self.assertEqual(job_options.path, "clip.wav")

    def test_main_window_builds_job_options_from_gui_settings_and_temp_media(self):
        runner = CapturingRunner()
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            window = MainWindow(store=MemoryStore(), monitor=False, runner=runner)
            window.settings.restore(
                {
                    "translate_enabled": True,
                    "language": "en",
                    "translate_to": "fr",
                    "translation_model": "m2m100-418m",
                    "translation_device": "cpu",
                    "context": "formal tone",
                    "glossary_text": '{"hello": "bonjour"}',
                    "output_txt": True,
                }
            )
            window.add_paths([str(media)])

            window.start_queue()

            self.assertTrue(wait_until(lambda: not window.running and window.worker is None, timeout_ms=3000))
            self.assertEqual(len(runner.options), 1)
            options = runner.options[0]
            self.assertEqual(options.path, str(media.resolve()))
            self.assertEqual(options.translate_to, "fr")
            self.assertEqual(dict(options.glossary), {"hello": "bonjour"})
            self.assertEqual(options.context, "formal tone")
            self.assertTrue(options.output_txt)

    def test_main_window_rejects_invalid_gui_options_before_worker_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            window = MainWindow(store=MemoryStore(), monitor=False, runner=CapturingRunner())
            window.settings.restore({"backend": "faster", "inference_batch_size": 4, "vad": False})
            window.add_paths([str(media)])

            with mock.patch.object(QMessageBox, "warning") as warning:
                window.start_queue()

            self.assertFalse(window.running)
            self.assertIsNone(window.worker)
            self.assertEqual(window.items[0].status, "queued")
            self.assertTrue(warning.called)
            self.assertIn("requires VAD", warning.call_args.args[2])


if __name__ == "__main__":
    unittest.main()

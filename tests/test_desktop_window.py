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

from auto_subtitle_plus.api import JobResult
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
    def __init__(self, settings=None, items=None):
        self.settings = settings or {}
        self.items = list(items or [])
        self.saved = []

    def load(self):
        return dict(self.settings), list(self.items)

    def save(self, settings, items):
        self.saved.append((dict(settings), [item.status for item in items]))


class FakeRunner:
    def __init__(self, wait_for_cancel=False):
        self.wait_for_cancel = wait_for_cancel
        self.calls = []
        self.cancel_seen = False
        self.closed = False

    def run(self, options, progress=None, cancel=None):
        self.calls.append(options)
        if progress is not None:
            progress({"state": "loading", "message": "Loading fake backend", "progress": None})
            progress({"state": "transcribing", "message": "Fake transcript", "progress": 0.5})
        if self.wait_for_cancel:
            while not (cancel and cancel()):
                time.sleep(0.01)
            self.cancel_seen = True
            return JobResult(status="cancelled", error="Job cancelled")
        if progress is not None:
            progress({"state": "completed", "message": "Fake complete", "progress": 1.0})
        return JobResult(status="completed", outputs=(str(Path(options.path).with_suffix(".srt")),), warnings=("fake warning",))

    def close(self):
        self.closed = True


class FailingRunner:
    def __init__(self, error):
        self.error = error

    def run(self, options, progress=None, cancel=None):
        if progress is not None:
            progress({"state": "writing", "message": "Writing subtitles", "progress": 0.9})
        return JobResult(status="failed", error=self.error)

    def close(self):
        return None


class DesktopWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        wait_until(lambda: all(not widget.isVisible() for widget in QApplication.topLevelWidgets()), timeout_ms=1000)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def make_window(self, runner=None, store=None):
        window = MainWindow(store=store or MemoryStore(), monitor=False, runner=runner)
        window.show()
        QApplication.processEvents()
        return window

    def test_add_paths_deduplicates_local_files_and_rejects_url_unc_and_unsupported_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            notes = Path(tmp) / "notes.txt"
            notes.write_text("not media", encoding="utf-8")
            window = self.make_window()

            added = window.add_paths([str(media), str(media), "https://example.test/clip.wav", r"\\server\share\clip.wav", str(notes)])

            self.assertEqual(added, 1)
            self.assertEqual(len(window.items), 1)
            self.assertEqual(window.items[0].path, str(media.resolve()))
            log = window.activity.toPlainText()
            self.assertIn("Added 1 file", log)
            self.assertIn("Only local files are accepted", log)
            self.assertIn("Unsupported media file", log)

    def test_reorder_remove_and_retry_are_disabled_while_running_but_work_when_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f"clip{index}.wav" for index in range(3)]
            for path in paths:
                write_tiny_wav(path)
            window = self.make_window()
            window.add_paths([str(path) for path in paths])

            window.table.selectRow(1)
            window.move_selected(-1)
            self.assertEqual(Path(window.items[0].path).name, "clip1.wav")

            window.items[0].status = "failed"
            window.items[0].stage = "Failed"
            window.items[0].progress = 0.0
            window.items[0].error = "temporary failure"
            window.table.selectRow(0)
            window.retry_selected()
            self.assertEqual(window.items[0].status, "queued")
            self.assertEqual(window.items[0].stage, "Queued")
            self.assertEqual(window.items[0].error, "")

            window.running = True
            window.table.selectRow(1)
            before = [item.path for item in window.items]
            window.remove_selected()
            window.move_selected(1)
            self.assertEqual([item.path for item in window.items], before)

            window.running = False
            window.table.selectRow(1)
            removed = window.items[1].path
            window.remove_selected()
            self.assertNotIn(removed, [item.path for item in window.items])

    def test_fake_job_runner_updates_progress_and_completes_queue_without_real_backend(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "first.wav", Path(tmp) / "second.wav"]
            for path in paths:
                write_tiny_wav(path)
            window = self.make_window(runner=runner)
            window.add_paths([str(path) for path in paths])

            window.start_queue()

            self.assertTrue(wait_until(lambda: not window.running and window.worker is None, timeout_ms=4000))
            self.assertEqual([item.status for item in window.items], ["completed", "completed"])
            self.assertEqual(len(runner.calls), 2)
            self.assertEqual(window.current_progress.value(), 100)
            self.assertIn("Fake complete", window.activity.toPlainText())
            self.assertIn("fake warning", window.activity.toPlainText())

    def test_close_cancels_active_job_and_leaves_queued_items_pending_without_qthread_leak(self):
        runner = FakeRunner(wait_for_cancel=True)
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "active.wav", Path(tmp) / "queued.wav"]
            for path in paths:
                write_tiny_wav(path)
            window = self.make_window(runner=runner)
            window.add_paths([str(path) for path in paths])
            window.start_queue()
            self.assertTrue(wait_until(lambda: window.worker is not None and window.worker.isRunning(), timeout_ms=2000))

            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                window.close()

            self.assertTrue(wait_until(lambda: runner.cancel_seen and window.worker is None and runner.closed, timeout_ms=4000))
            self.assertEqual([item.status for item in window.items], ["cancelled", "queued"])
            self.assertTrue(runner.closed)
            self.assertFalse(window.isVisible())

    def test_failed_job_row_keeps_actual_error_cause_instead_of_generic_failure(self):
        error = "File write error: output denied: C:/out/clip.srt"
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            window = self.make_window(runner=FailingRunner(error))
            window.add_paths([str(media)])

            window.start_queue()

            self.assertTrue(wait_until(lambda: not window.running and window.worker is None, timeout_ms=3000))
            self.assertEqual(window.items[0].status, "failed")
            self.assertEqual(window.items[0].stage, "Failed")
            self.assertEqual(window.items[0].progress, 0.0)
            self.assertEqual(window.items[0].error, error)
            self.assertIn(error, window.activity.toPlainText())
            self.assertEqual(window.current_progress.value(), 0)
            self.assertEqual(window.current_label.text(), "Queue finished — 1 failed")

            window.table.selectRow(0)
            window.show_selected_outputs()
            self.assertEqual(window.outputs.count(), 1)
            self.assertEqual(window.outputs.item(0).text(), error)

            window.runner = FakeRunner()
            window.retry_selected()
            window.start_queue()
            self.assertTrue(wait_until(lambda: not window.running and window.worker is None))
            self.assertEqual(window.current_progress.value(), 100)
            self.assertEqual(window.current_label.text(), "Queue finished")

    def test_pause_and_cancel_menu_actions_follow_buttons_when_queue_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            window = self.make_window(runner=FakeRunner(wait_for_cancel=True))
            window.add_paths([str(media)])
            window.start_queue()
            self.assertTrue(wait_until(lambda: window.worker is not None and window.worker.isRunning()))
            window.pause_queue()
            window.update_controls()
            self.assertFalse(window.pause_action.isEnabled())
            self.assertFalse(window.pause_button.isEnabled())
            self.assertEqual(window.pause_button.text(), "Pausing after file")
            window.cancel_current()
            window.update_controls()
            self.assertFalse(window.cancel_action.isEnabled())
            self.assertFalse(window.cancel_button.isEnabled())
            self.assertTrue(wait_until(lambda: not window.running and window.worker is None))


if __name__ == "__main__":
    unittest.main()

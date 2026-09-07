import json
import tempfile
import unittest
from pathlib import Path

from auto_subtitle_plus.desktop.state import QueueItem, StateStore, local_media_path


def write_tiny_wav(path: Path) -> None:
    path.write_bytes(
        b"RIFF$\x00\x00\x00WAVEfmt "
        b"\x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00"
        b"data\x00\x00\x00\x00"
    )


class DesktopStateTests(unittest.TestCase):
    def test_malformed_optional_row_fields_do_not_break_restored_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"version": 1, "queue": [{"path": "C:/clip.wav", "status": "failed", "outputs": None, "elapsed": "bad", "progress": {}, "error": [], "stage": {}}]}), encoding="utf-8")
            _, items = StateStore(path).load()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].outputs, [])
        self.assertEqual(items[0].elapsed, 0)
        self.assertEqual(items[0].progress, 0)
        self.assertEqual(items[0].error, "")

    def test_local_media_path_accepts_existing_media_and_rejects_remote_or_unc_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            write_tiny_wav(media)
            notes = Path(tmp) / "notes.txt"
            notes.write_text("not media", encoding="utf-8")

            self.assertEqual(local_media_path(str(media)), str(media.resolve()))

            with self.assertRaisesRegex(ValueError, "Only local files"):
                local_media_path("https://example.test/clip.wav")
            with self.assertRaisesRegex(ValueError, "Only local files"):
                local_media_path(r"\\server\share\clip.wav")
            with self.assertRaisesRegex(ValueError, "Unsupported media"):
                local_media_path(str(notes))

    def test_state_store_round_trips_settings_and_queue_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            item = QueueItem("C:/media/clip.wav", status="failed", error="boom", outputs=["C:/out/clip.srt"])

            store.save({"backend": "faster", "model": "small"}, [item])
            settings, items = store.load()

        self.assertEqual(settings, {"backend": "faster", "model": "small"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, "C:/media/clip.wav")
        self.assertEqual(items[0].status, "failed")
        self.assertEqual(items[0].outputs, ["C:/out/clip.srt"])

    def test_load_recovers_interrupted_work_without_restarting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "settings": {"model": "small"},
                        "queue": [
                            {"path": "C:/media/running.wav", "status": "running", "stage": "Transcribing", "progress": 0.4},
                            {"path": "C:/media/queued.wav", "status": "queued", "stage": "Queued", "progress": 0.0},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            settings, items = StateStore(path).load()

        self.assertEqual(settings, {"model": "small"})
        self.assertEqual([item.status for item in items], ["interrupted", "queued"])
        self.assertEqual(items[0].stage, "Interrupted")
        self.assertEqual(items[0].progress, 0.0)

    def test_load_ignores_missing_corrupt_or_oversized_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = StateStore(Path(tmp) / "missing.json")
            corrupt_path = Path(tmp) / "corrupt.json"
            corrupt_path.write_text("{", encoding="utf-8")
            large_path = Path(tmp) / "large.json"
            large_path.write_text(" " * 2_000_001, encoding="utf-8")

            self.assertEqual(missing.load(), ({}, []))
            self.assertEqual(StateStore(corrupt_path).load(), ({}, []))
            self.assertEqual(StateStore(large_path).load(), ({}, []))


if __name__ == "__main__":
    unittest.main()

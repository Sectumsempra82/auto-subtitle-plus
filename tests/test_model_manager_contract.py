import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import filelock

from auto_subtitle_plus import model_manager


def hanging_worker(result):
    time.sleep(10)
    result.put(None)


class ModelManagerContractTests(unittest.TestCase):
    def tiny_gguf_spec(self):
        return model_manager.ModelSpec(
            id="tiny-local",
            repo_id="example/tiny-local",
            revision="abc123def4567890",
            family="hy-mt2",
            files=(
                model_manager.ModelFile(
                    name="model.gguf",
                    size=4,
                    sha256="3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7",
                    git_blob="unused",
                ),
            ),
            license="apache-2.0",
            contextual=True,
        )

    def tiny_prepared_spec(self):
        return model_manager.ModelSpec(
            id="tiny-opus-en-fr",
            repo_id="example/opus-mt-en-fr",
            revision="def456abc1237890",
            family="opus",
            source="en",
            target="fr",
            files=(
                model_manager.ModelFile(
                    name="README.md",
                    size=4,
                    sha256=None,
                    git_blob=hashlib.sha1(b"blob 4\0data").hexdigest(),
                ),
            ),
            license="apache-2.0",
            contextual=False,
        )

    def test_normalize_language_accepts_codes_and_catalog_names_only(self):
        self.assertEqual(model_manager.normalize_language("Italian"), "it")
        self.assertEqual(model_manager.normalize_language("FR"), "fr")

        with self.assertRaisesRegex(ValueError, "validated translation catalog"):
            model_manager.normalize_language("klingon")

    def test_language_catalog_is_currently_limited_to_validated_six_language_set(self):
        self.assertEqual(
            set(model_manager.LANGUAGES),
            {"en", "it", "fr", "es", "de", "pt"},
        )

    def test_resolve_model_maps_opus_alias_to_directional_catalog_entry(self):
        spec = model_manager.resolve_model("opus-mt", "en", "fr")

        self.assertEqual(spec.id, "opus-en-fr")
        self.assertEqual(spec.source, "en")
        self.assertEqual(spec.target, "fr")

        with self.assertRaisesRegex(ValueError, "only supports"):
            model_manager.resolve_model("opus-en-fr", "fr", "en")

    def test_resolve_model_rejects_unknown_catalog_id_and_unknown_language(self):
        with self.assertRaisesRegex(ValueError, "No catalog model"):
            model_manager.resolve_model("missing-model", "en", "fr")

        with self.assertRaisesRegex(ValueError, "validated translation catalog"):
            model_manager.resolve_model("opus-mt", "en", "ja")

    def test_list_models_keeps_via_english_explicit_and_excludes_directional_opus(self):
        direct = model_manager.list_models("en", "fr", route="direct")
        via_english = model_manager.list_models("it", "de", route="via-en")
        source_or_target_already_english = model_manager.list_models("en", "fr", route="via-en")

        self.assertTrue(any(spec.id == "opus-en-fr" for spec in direct))
        self.assertFalse(any(spec.family == "opus" for spec in via_english))
        self.assertEqual(source_or_target_already_english, [])

        with self.assertRaisesRegex(ValueError, "Unknown translation route"):
            model_manager.list_models("it", "de", route="pivot")

    def test_verify_file_accepts_exact_sha256_or_exact_git_blob_sha1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            path.write_bytes(b"data")

            sha256_item = model_manager.ModelFile(
                name="file.bin",
                size=4,
                sha256=hashlib.sha256(b"data").hexdigest(),
                git_blob="unused",
            )
            git_blob_item = model_manager.ModelFile(
                name="file.bin",
                size=4,
                sha256=None,
                git_blob=hashlib.sha1(b"blob 4\0data").hexdigest(),
            )
            wrong_item = model_manager.ModelFile(
                name="file.bin",
                size=4,
                sha256=hashlib.sha256(b"nope").hexdigest(),
                git_blob=hashlib.sha1(b"blob 4\0nope").hexdigest(),
            )

            self.assertTrue(model_manager.verify_file(path, sha256_item))
            self.assertTrue(model_manager.verify_file(path, git_blob_item))
            self.assertFalse(model_manager.verify_file(path, wrong_item))

    def test_ensure_model_offline_rejects_missing_or_corrupt_files_without_download(self):
        spec = self.tiny_gguf_spec()

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("download attempted")):
                with self.assertRaisesRegex(RuntimeError, "offline mode forbids downloads"):
                    model_manager.ensure_model(spec, offline=True, cache_dir=tmp)

    def test_ensure_model_rejects_download_when_disk_space_is_insufficient(self):
        spec = self.tiny_gguf_spec()

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(model_manager.shutil, "disk_usage", return_value=SimpleNamespace(free=1)), \
                 mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("download attempted")):
                with self.assertRaisesRegex(RuntimeError, "Insufficient disk space to download tiny-local"):
                    model_manager.ensure_model(spec, offline=False, cache_dir=tmp)

    def test_ensure_model_uses_installed_verified_gguf_without_network_worker(self):
        spec = self.tiny_gguf_spec()
        progress_events = []

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "model.gguf").write_bytes(b"data")

            with mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("download attempted")):
                path = model_manager.ensure_model(
                    spec,
                    offline=True,
                    progress=progress_events.append,
                    cache_dir=tmp,
                )

        self.assertEqual(path.name, "model.gguf")
        self.assertEqual(progress_events[-1]["state"], "ready")
        self.assertEqual(progress_events[-1]["model"], "tiny-local")

    def test_ensure_model_does_not_delete_corrupt_files_in_offline_mode(self):
        spec = self.tiny_gguf_spec()

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            corrupt = raw / "model.gguf"
            corrupt.write_bytes(b"bad!")

            with mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("download attempted")):
                with self.assertRaisesRegex(RuntimeError, "offline mode forbids downloads"):
                    model_manager.ensure_model(spec, offline=True, cache_dir=tmp)

            self.assertEqual(corrupt.read_bytes(), b"bad!")

    def test_prepared_receipt_must_match_exact_prepared_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = Path(tmp) / "prepared"
            prepared.mkdir()
            (prepared / "model.bin").write_bytes(b"data")
            (prepared / "receipt.json").write_text(
                "["
                '{"name":"model.bin","size":4,'
                f'"sha256":"{hashlib.sha256(b"data").hexdigest()}",'
                '"git_blob":""}'
                "]",
                encoding="utf-8",
            )

            self.assertTrue(model_manager._prepared_valid(prepared))
            (prepared / "model.bin").write_bytes(b"bad!")
            self.assertFalse(model_manager._prepared_valid(prepared))

    def test_ensure_model_uses_valid_prepared_receipt_without_convert_worker(self):
        spec = self.tiny_prepared_spec()
        progress_events = []

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")
            prepared = model_manager.prepared_dir(spec, tmp)
            prepared.mkdir(parents=True)
            (prepared / "model.bin").write_bytes(b"data")
            (prepared / "receipt.json").write_text(
                "["
                '{"name":"model.bin","size":4,'
                f'"sha256":"{hashlib.sha256(b"data").hexdigest()}",'
                '"git_blob":""}'
                "]",
                encoding="utf-8",
            )

            with mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("worker attempted")):
                path = model_manager.ensure_model(
                    spec,
                    offline=True,
                    progress=progress_events.append,
                    cache_dir=tmp,
                )

        self.assertEqual(path.name, prepared.name)
        self.assertEqual(progress_events[-1]["state"], "ready")

    def test_ensure_model_rejects_conversion_when_disk_space_is_insufficient(self):
        spec = self.tiny_prepared_spec()

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")

            with mock.patch.object(model_manager.shutil, "disk_usage", return_value=SimpleNamespace(free=1)), \
                 mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("conversion attempted")):
                with self.assertRaisesRegex(RuntimeError, "Insufficient free disk space for one-time model conversion"):
                    model_manager.ensure_model(spec, offline=False, cache_dir=tmp)

    def test_incomplete_conversion_output_is_never_marked_installed(self):
        spec = self.tiny_prepared_spec()

        def incomplete_convert(_target, args, *_unused, **_kwargs):
            destination = Path(args[1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "tokenizer.json").write_bytes(b"partial")

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")

            with mock.patch.object(model_manager, "_run_worker", side_effect=incomplete_convert):
                with self.assertRaisesRegex(RuntimeError, "Conversion did not produce model.bin"):
                    model_manager.ensure_model(spec, offline=False, cache_dir=tmp)

            prepared = model_manager.prepared_dir(spec, tmp)
            self.assertFalse(prepared.exists())
            self.assertEqual(model_manager.model_metadata(spec, tmp)["status"], "not-installed")

    def test_native_conversion_worker_exit_retries_once_and_promotes_successful_retry(self):
        spec = self.tiny_prepared_spec()
        progress_events = []
        attempts = []

        def flaky_convert(_target, args, *_unused, **_kwargs):
            attempts.append(args)
            destination = Path(args[1])
            if len(attempts) == 1:
                raise RuntimeError("Model worker exited with code -1073741819")
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "model.bin").write_bytes(b"converted")

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")

            with mock.patch.object(model_manager, "_run_worker", side_effect=flaky_convert):
                path = model_manager.ensure_model(
                    spec,
                    offline=False,
                    progress=progress_events.append,
                    cache_dir=tmp,
                )

            self.assertEqual(len(attempts), 2)
            self.assertEqual(path.name, model_manager.prepared_dir(spec, tmp).name)
            self.assertTrue((path / "model.bin").is_file())
            self.assertTrue(model_manager._prepared_valid(path))
            self.assertIn(
                "Model conversion worker stopped unexpectedly; retrying preparation once",
                [event.get("message") for event in progress_events],
            )

    def test_native_conversion_worker_exit_is_not_retried_more_than_once(self):
        spec = self.tiny_prepared_spec()
        attempts = []

        def always_exits(_target, args, *_unused, **_kwargs):
            attempts.append(args)
            raise RuntimeError("Model worker exited with code -1073741819")

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")

            with mock.patch.object(model_manager, "_run_worker", side_effect=always_exits):
                with self.assertRaisesRegex(RuntimeError, "Model worker exited with code -1073741819"):
                    model_manager.ensure_model(spec, offline=False, cache_dir=tmp)

            self.assertEqual(len(attempts), 2)

    def test_ensure_model_emits_waiting_when_another_process_holds_model_lock(self):
        spec = self.tiny_gguf_spec()
        progress_events = []

        class DelayedLock:
            def __init__(self, _path):
                self.attempts = 0

            def acquire(self, timeout):
                self.attempts += 1
                if self.attempts == 1:
                    raise filelock.Timeout("locked")

            def release(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "model.gguf").write_bytes(b"data")

            with mock.patch("filelock.FileLock", DelayedLock), \
                 mock.patch.object(model_manager, "_run_worker", side_effect=AssertionError("download attempted")):
                path = model_manager.ensure_model(
                    spec,
                    offline=True,
                    progress=progress_events.append,
                    cache_dir=tmp,
                )

        self.assertEqual(path.name, "model.gguf")
        self.assertEqual([event["state"] for event in progress_events], ["waiting", "ready"])

    def test_concurrent_ensure_model_requests_are_serialized_by_catalog_lock(self):
        spec = self.tiny_gguf_spec()
        progress_events = []
        attempts = []
        barrier = threading.Barrier(2)
        errors = []
        results = []

        def fake_download(_target, args, *_unused, **_kwargs):
            attempts.append(args)
            raw = Path(args[1])
            time.sleep(0.5)
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "model.gguf").write_bytes(b"data")

        def worker(tmp):
            try:
                barrier.wait(timeout=5)
                results.append(
                    model_manager.ensure_model(
                        spec,
                        offline=False,
                        progress=progress_events.append,
                        cache_dir=tmp,
                    )
                )
            except BaseException as error:
                errors.append(error)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(model_manager, "_run_worker", side_effect=fake_download):
                threads = [threading.Thread(target=worker, args=(tmp,)) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertFalse(any(thread.is_alive() for thread in threads))

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(attempts), 1)
        self.assertIn("waiting", [event["state"] for event in progress_events])

    def test_run_worker_cancels_spawned_process_when_cancelled(self):
        with self.assertRaises(model_manager.CancelledError):
            model_manager._run_worker(
                hanging_worker,
                (),
                cancel=lambda: True,
                state="downloading",
            )

    def test_model_metadata_reports_installed_status_from_verified_files(self):
        spec = self.tiny_gguf_spec()

        with tempfile.TemporaryDirectory() as tmp:
            missing = model_manager.model_metadata(spec, tmp)
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "model.gguf").write_bytes(b"data")
            installed = model_manager.model_metadata(spec, tmp)

        self.assertEqual(missing["status"], "not-installed")
        self.assertEqual(installed["status"], "installed")
        self.assertEqual(installed["context_mode"], "dialogue")
        self.assertEqual(installed["download_size"], 4)

    def test_model_metadata_does_not_report_corrupt_prepared_receipt_as_installed(self):
        spec = self.tiny_prepared_spec()

        with tempfile.TemporaryDirectory() as tmp:
            raw = model_manager.source_dir(spec, tmp)
            raw.mkdir(parents=True)
            (raw / "README.md").write_bytes(b"data")
            prepared = model_manager.prepared_dir(spec, tmp)
            prepared.mkdir(parents=True)
            (prepared / "receipt.json").write_text(
                "["
                '{"name":"model.bin","size":4,'
                f'"sha256":"{hashlib.sha256(b"data").hexdigest()}",'
                '"git_blob":""}'
                "]",
                encoding="utf-8",
            )

            metadata = model_manager.model_metadata(spec, tmp)

        self.assertEqual(metadata["status"], "not-installed")


if __name__ == "__main__":
    unittest.main()

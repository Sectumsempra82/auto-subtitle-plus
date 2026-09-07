import argparse
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import benchmark, cli


DISTIL_ALIAS = "distil-large-v3.5"
DISTIL_CANONICAL_ID = "distil-whisper/distil-large-v3.5-ct2"


class DistilModelContractTests(unittest.TestCase):
    def import_backends(self):
        from auto_subtitle_plus import backends

        return backends

    def namespace(self, **overrides):
        values = {
            "backend": "faster",
            "model": DISTIL_ALIAS,
            "models": [DISTIL_ALIAS],
            "device": "cpu",
            "compute_type": "int8",
            "inference_batch_size": 1,
            "vad": False,
            "enhance_consistency": False,
            "language": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def assert_valid(self, backends, args):
        parser = argparse.ArgumentParser()
        backends.validate_backend_options(args, parser)

    def test_distil_alias_resolves_to_canonical_faster_model_and_default_english(self):
        backends = self.import_backends()
        calls = {}

        class FakeWhisperModel:
            def __init__(self, model_name, device, compute_type):
                calls["init"] = (model_name, device, compute_type)

            def transcribe(self, audio_path, **kwargs):
                calls["transcribe"] = (audio_path, kwargs)
                return iter([types.SimpleNamespace(start=0.0, end=1.0, text="hello")]), types.SimpleNamespace(duration=1.0)

        args = self.namespace(language=None)
        self.assert_valid(backends, args)
        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(args)
            result = model.transcribe(
                "clip.wav",
                language=args.language,
                verbose=False,
                condition_on_previous_text=False,
                word_timestamps=False,
            )

        self.assertEqual(calls["init"], (DISTIL_CANONICAL_ID, "cpu", "int8"))
        self.assertEqual(calls["transcribe"][1]["language"], "en")
        self.assertEqual(result["segments"][0]["text"], "hello")

    def test_distil_canonical_id_accepts_en_and_english(self):
        backends = self.import_backends()
        for language in ("en", "english", "English"):
            with self.subTest(language=language):
                args = self.namespace(model=DISTIL_CANONICAL_ID, models=[DISTIL_CANONICAL_ID], language=language)
                self.assert_valid(backends, args)
                self.assertEqual(args.model, DISTIL_CANONICAL_ID)
                self.assertEqual(args.language, "en")

    def test_distil_rejects_non_english_before_model_load(self):
        backends = self.import_backends()
        parser = argparse.ArgumentParser()
        args = self.namespace(language="fr")

        with mock.patch.object(backends, "load_backend_model") as load_backend_model:
            with self.assertRaises(SystemExit):
                backends.validate_backend_options(args, parser)

        load_backend_model.assert_not_called()

    def test_stable_backend_rejects_distil_alias_with_faster_guidance(self):
        backends = self.import_backends()
        parser = argparse.ArgumentParser()
        args = self.namespace(backend="stable", language="en")

        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                backends.validate_backend_options(args, parser)

        message = stderr.getvalue().lower()
        self.assertIn("faster", message)
        self.assertIn(DISTIL_ALIAS, message)

    def test_cli_list_models_works_without_paths_and_without_model_load(self):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["auto_subtitle_plus", "--backend", "faster", "--list-models"]), \
             mock.patch.object(cli, "load_backend_model", side_effect=AssertionError("should not load")), \
             mock.patch("sys.stdout", stdout):
            exit_code = cli.main()

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn(DISTIL_ALIAS, output)
        self.assertIn(DISTIL_CANONICAL_ID, output)
        self.assertIn("English", output)

    def test_cli_without_paths_still_rejects_when_not_listing(self):
        with mock.patch.object(sys, "argv", ["auto_subtitle_plus", "--backend", "faster"]):
            exit_code = cli.main()

        self.assertNotEqual(exit_code, 0)

    def test_benchmark_report_records_effective_distil_model_and_language(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]
        }

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.wav"
            reference = Path(tmp) / "reference.srt"
            output = Path(tmp) / "report.json"
            audio.write_bytes(b"fake")
            reference.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n\n",
                encoding="utf-8",
            )

            argv = [
                "auto_subtitle_benchmark",
                str(audio),
                "--reference-srt",
                str(reference),
                "--output-json",
                str(output),
                "--models",
                DISTIL_ALIAS,
                "--backend",
                "faster",
                "--device",
                "cpu",
                "--compute-type",
                "int8",
            ]

            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(benchmark, "probe_duration", return_value=1.0), \
                 mock.patch.object(benchmark, "load_backend_model", return_value=fake_model), \
                 mock.patch.object(benchmark.time, "perf_counter", side_effect=[1.0, 2.0, 3.0, 4.0, 5.0]):
                exit_code = benchmark.main()

            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["backend"], "faster")
        self.assertEqual(report["language"], "en")
        self.assertEqual(report["models"][0]["model"], DISTIL_ALIAS)
        self.assertEqual(report["models"][0]["effective_model"], DISTIL_CANONICAL_ID)
        fake_model.transcribe.assert_called_once()
        self.assertEqual(fake_model.transcribe.call_args.kwargs["language"], "en")


if __name__ == "__main__":
    unittest.main()

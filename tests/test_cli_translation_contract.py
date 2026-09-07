import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import cli, processing
from auto_subtitle_plus.translation_types import SubtitleCue, TranslationResult


class FakeTranslationPipeline:
    requests = []

    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir

    def translate(self, request):
        self.requests.append(request)
        final = (
            SubtitleCue(
                id="cue-1-tr",
                source_ids=(request.cues[0].id,),
                start=request.cues[0].start,
                end=request.cues[0].end,
                text=f"{request.settings.target_language}:translated",
                language=request.settings.target_language,
                source_text=request.cues[0].text if request.settings.bilingual else None,
            ),
        )
        intermediate = ()
        if request.settings.route == "via-en":
            intermediate = (
                SubtitleCue(
                    id="cue-1-en",
                    source_ids=(request.cues[0].id,),
                    start=request.cues[0].start,
                    end=request.cues[0].end,
                    text="en:translated",
                    language="en",
                ),
            )
        return TranslationResult(
            source_key=request.source_key,
            source_language=request.settings.source_language,
            target_language=request.settings.target_language,
            route=request.settings.route,
            final_cues=final,
            source_cues=request.cues,
            intermediate_cues=intermediate,
        )

    def close(self):
        pass


class CliTranslationContractTests(unittest.TestCase):
    def setUp(self):
        FakeTranslationPipeline.requests = []

    def faster_args(self):
        return argparse.Namespace(
            backend="faster",
            model="turbo",
            language="it",
            device="cpu",
            compute_type="int8",
            inference_batch_size=1,
            vad=False,
            word_timestamps=False,
            enhance_consistency=False,
        )

    def faster_fingerprint(self, revision, available=True):
        return {
            "backend": "faster",
            "model": "turbo",
            "package": {
                "faster-whisper": "1.2.3",
                "ctranslate2": "4.5.6",
            },
            "fingerprint_available": available,
            "model_source": "huggingface-cache",
            "repo_id": "Systran/faster-whisper-large-v3-turbo",
            "model_revision": revision,
            "model_sha256": "sha-for-" + revision,
        }

    def run_cli(self, argv, language="it", text="ciao"):
        def fake_worker(_path, _audio_path, _source_key, _args):
            return {
                "ok": True,
                "language": language,
                "cues": [
                    {
                        "id": "cue-1",
                        "index": 1,
                        "start": 0.0,
                        "end": 1.0,
                        "text": text,
                        "language": language,
                        "speaker": None,
                        "words": [],
                        "metadata": {},
                    }
                ],
            }

        with mock.patch.object(sys, "argv", argv), \
             mock.patch.object(processing.glob, "glob", side_effect=lambda value: [value]), \
             mock.patch.object(processing, "is_audio", return_value=True), \
             mock.patch.object(processing, "default_device", return_value="cpu"), \
             mock.patch.object(cli, "validate_backend_options"), \
             mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("in-process ASR loaded")), \
             mock.patch.object(processing, "run_transcription_worker", side_effect=fake_worker) as worker, \
             mock.patch.object(processing, "TranslationPipeline", FakeTranslationPipeline), \
             mock.patch.object(processing, "close_translation_runtime"):
            return cli.main(), worker

    def test_cli_translate_to_defaults_to_local_direct_and_writes_final_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-cli-local-direct")
            output = Path(tmp) / "out"
            cache = Path(tmp) / "cache"
            exit_code, worker = self.run_cli(
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--output-srt",
                    "--output-txt",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(cache),
                ],
            )

            srt = (output / "clip.srt").read_text(encoding="utf-8")
            txt = (output / "clip.txt").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(worker.call_count, 1)
        settings = FakeTranslationPipeline.requests[0].settings
        self.assertEqual(settings.engine, "local")
        self.assertEqual(settings.route, "direct")
        self.assertEqual(settings.target_language, "fr")
        self.assertIn("fr:translated", srt)
        self.assertNotIn("ciao", srt)
        self.assertEqual(txt.strip(), "fr:translated")

    def test_cli_progress_json_stdout_lines_are_json_with_completed_and_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            output = Path(tmp) / "out"
            script = Path(tmp) / "run_cli_progress_json.py"
            repo = Path(__file__).resolve().parents[1]
            script.write_text(
                f"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, {str(repo)!r})

from auto_subtitle_plus import cli, processing

class FakeModel:
    def transcribe(self, *_args, **_kwargs):
        print("backend raw stdout noise")
        return {{"language": "en", "segments": [{{"start": 0.0, "end": 1.0, "text": "hello"}}]}}
    def close(self):
        pass

fingerprint = {{
    "backend": "stable",
    "model": "small",
    "fingerprint_available": True,
    "model_source": "test",
    "model_revision": "revision",
    "model_sha256": "sha256",
    "package": {{}},
}}

sys.argv = [
    "auto_subtitle_plus",
    {str(media)!r},
    "--output-srt",
    "--output-dir",
    {str(output)!r},
    "--device",
    "cpu",
    "--progress-json",
    "--resources",
]

with mock.patch.object(cli, "validate_backend_options"), \\
     mock.patch.object(processing, "validate_backend_options"), \\
     mock.patch.object(processing, "load_backend_model", return_value=FakeModel()), \\
     mock.patch.object(processing, "asr_backend_fingerprint", return_value=fingerprint):
    raise SystemExit(cli.main())
""",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertGreater(len(stdout_lines), 0)
        events = [json.loads(line) for line in stdout_lines]
        self.assertTrue(all(isinstance(event, dict) and "state" in event for event in events))
        self.assertIn("completed", [event["state"] for event in events])
        self.assertIn("resources", [event["state"] for event in events])
        self.assertNotIn("backend raw stdout noise", completed.stdout)
        self.assertIn("backend raw stdout noise", completed.stderr)

    def test_faster_source_cache_requires_resolved_snapshot_and_keys_by_revision(self):
        args = self.faster_args()

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"same-audio-content")

            with mock.patch.object(
                processing,
                "asr_backend_fingerprint",
                return_value=self.faster_fingerprint("unknown", available=False),
            ):
                self.assertIsNone(processing.cacheable_source_stage_settings(args))

            with mock.patch.object(processing, "asr_backend_fingerprint", return_value=self.faster_fingerprint("snapshot-a")):
                settings_a = processing.cacheable_source_stage_settings(args)
                key_a = processing.source_cache_key(str(media), settings_a)

            with mock.patch.object(processing, "asr_backend_fingerprint", return_value=self.faster_fingerprint("snapshot-b")):
                settings_b = processing.cacheable_source_stage_settings(args)
                key_b = processing.source_cache_key(str(media), settings_b)

        self.assertIsNotNone(settings_a)
        self.assertEqual(settings_a["asr_fingerprint"]["repo_id"], "Systran/faster-whisper-large-v3-turbo")
        self.assertEqual(settings_a["asr_fingerprint"]["model_revision"], "snapshot-a")
        self.assertNotEqual(key_a, key_b)

    def test_cli_google_translation_is_explicit_and_offline_google_is_rejected_before_model_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-cli-google")
            output = Path(tmp) / "out"
            exit_code, _worker = self.run_cli(
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--translation-backend",
                    "google",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(Path(tmp) / "cache"),
                ],
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(FakeTranslationPipeline.requests[0].settings.engine, "google")

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-cli-google-offline")
            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--translation-backend",
                    "google",
                    "--offline",
                ],
            ), mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("model loaded")):
                with self.assertRaises(SystemExit):
                    cli.main()

    def test_cli_via_english_rejects_english_source_or_target(self):
        for language, target in (("en", "fr"), ("it", "en")):
            with self.subTest(language=language, target=target):
                with tempfile.TemporaryDirectory() as tmp:
                    media = Path(tmp) / "clip.wav"
                    media.write_bytes(f"{language}-{target}".encode("utf-8"))
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "auto_subtitle_plus",
                            str(media),
                            "--language",
                            language,
                            "--translate-to",
                            target,
                            "--translation-route",
                            "via-en",
                        ],
                    ), mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("model loaded")):
                        with self.assertRaises(SystemExit):
                            cli.main()

    def test_cli_writes_optional_source_and_intermediate_subtitles_for_via_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-cli-via-en")
            output = Path(tmp) / "out"
            exit_code, _worker = self.run_cli(
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--translation-route",
                    "via-en",
                    "--output-source-subtitles",
                    "--output-intermediate-subtitles",
                    "--output-txt",
                    "--output-srt",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(Path(tmp) / "cache"),
                ],
            )

            source = (output / "clip.source.it.srt").read_text(encoding="utf-8")
            source_txt = (output / "clip.source.it.txt").read_text(encoding="utf-8")
            intermediate = (output / "clip.intermediate.en.srt").read_text(encoding="utf-8")
            intermediate_txt = (output / "clip.intermediate.en.txt").read_text(encoding="utf-8")
            final = (output / "clip.srt").read_text(encoding="utf-8")
            final_txt = (output / "clip.txt").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(FakeTranslationPipeline.requests[0].settings.route, "via-en")
        self.assertIn("ciao", source)
        self.assertEqual(source_txt.strip(), "ciao")
        self.assertIn("en:translated", intermediate)
        self.assertEqual(intermediate_txt.strip(), "en:translated")
        self.assertIn("fr:translated", final)
        self.assertEqual(final_txt.strip(), "fr:translated")

    def test_cli_retry_translation_uses_cached_source_without_loading_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-cli-retry")
            output = Path(tmp) / "out"
            cache = Path(tmp) / "cache"

            first_exit, worker = self.run_cli(
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(cache),
                ],
            )
            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--retry-translation",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(cache),
                ],
            ), mock.patch.object(processing.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(processing, "default_device", return_value="cpu"), \
                 mock.patch.object(cli, "validate_backend_options"), \
                 mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("ASR loaded")), \
                 mock.patch.object(processing, "TranslationPipeline", FakeTranslationPipeline), \
                 mock.patch.object(processing, "close_translation_runtime"):
                retry_exit = cli.main()

        self.assertEqual(first_exit, 0)
        self.assertEqual(retry_exit, 0)
        self.assertEqual(worker.call_count, 1)

    def test_cli_retry_translation_uses_resolved_faster_snapshot_cache_without_asr_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"unique-faster-snapshot-retry")
            output = Path(tmp) / "out"
            cache = Path(tmp) / "cache"
            args = self.faster_args()
            args.translation_cache_dir = str(cache)
            resolved_fingerprint = self.faster_fingerprint("snapshot-ready")

            with mock.patch.object(processing, "asr_backend_fingerprint", return_value=resolved_fingerprint):
                cache_settings = processing.cacheable_source_stage_settings(args)
                source_key = processing.source_cache_key(str(media), cache_settings)

            cues = processing.source_cues_from_transcript(
                {"language": "it", "segments": [{"start": 0.0, "end": 1.0, "text": "ciao"}]},
                source_key,
                "it",
            )
            processing.cache_source_transcript(str(cache), source_key, cues, "it")

            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--backend",
                    "faster",
                    "--model",
                    "turbo",
                    "--compute-type",
                    "int8",
                    "--language",
                    "it",
                    "--translate-to",
                    "fr",
                    "--retry-translation",
                    "--output-dir",
                    str(output),
                    "--translation-cache-dir",
                    str(cache),
                ],
            ), mock.patch.object(processing.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(processing, "default_device", return_value="cpu"), \
                 mock.patch.object(cli, "validate_backend_options"), \
                 mock.patch.object(processing, "asr_backend_fingerprint", return_value=resolved_fingerprint), \
                 mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("ASR model loaded")), \
                 mock.patch.object(processing, "run_transcription_worker", side_effect=AssertionError("ASR worker loaded")), \
                 mock.patch.object(processing, "TranslationPipeline", FakeTranslationPipeline), \
                 mock.patch.object(processing, "close_translation_runtime"):
                retry_exit = cli.main()

        self.assertEqual(retry_exit, 0)
        self.assertEqual(FakeTranslationPipeline.requests[0].source_key, source_key)


if __name__ == "__main__":
    unittest.main()

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import processing
from auto_subtitle_plus.api import JobOptions, JobResult, JobRunner


class FakeTranslationPipeline:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir

    def translate(self, request):
        from auto_subtitle_plus.translation_types import SubtitleCue, TranslationResult

        final = tuple(
            SubtitleCue(
                id=f"{cue.id}-tr",
                source_ids=(cue.id,),
                start=cue.start,
                end=cue.end,
                text=f"{request.settings.target_language}:{cue.text}",
                language=request.settings.target_language,
            )
            for cue in request.cues
        )
        return TranslationResult(
            source_key=request.source_key,
            source_language=request.settings.source_language or "it",
            target_language=request.settings.target_language,
            route=request.settings.route,
            final_cues=final,
            source_cues=request.cues,
        )

    def close(self):
        pass


class ApplicationApiContractTests(unittest.TestCase):
    def test_job_options_round_trip_serializes_plain_mapping(self):
        options = JobOptions.from_mapping(
            {
                "path": "clip.wav",
                "outputdir": "out",
                "translate_to": "fr",
                "context": "Speaker names: Ada and Grace",
                "glossary": {"ciao": "salut"},
                "overwrite": True,
            }
        )

        payload = options.to_mapping()
        round_trip = JobOptions.from_mapping(payload)

        self.assertEqual(options.path, "clip.wav")
        self.assertEqual(payload["output_dir"], "out")
        self.assertNotIn("outputdir", payload)
        self.assertEqual(payload["glossary"], {"ciao": "salut"})
        self.assertEqual(round_trip, options)

    def test_processing_resolves_api_output_dir_none_to_input_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            options = JobOptions(path=str(media), output_srt=True, overwrite=True)
            seen = {}

            def fake_run_job(normalized, context, pipeline):
                seen["output_dir"] = normalized.output_dir
                return processing.ProcessingResult(status="completed")

            with mock.patch.object(processing, "validate_backend_options"), \
                 mock.patch.object(processing, "_run_job", side_effect=fake_run_job):
                result = processing.run_job(
                    argparse.Namespace(**{**options.to_mapping(), "paths": [options.path]}),
                    stdout=None,
                )

        self.assertEqual(result.status, "completed")
        self.assertEqual(seen["output_dir"], str(Path(tmp).resolve()))

    def test_processing_prevents_output_overwrite_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            (Path(tmp) / "clip.srt").write_text("existing", encoding="utf-8")
            options = JobOptions(path=str(media), output_dir=tmp, output_srt=True, overwrite=False)

            result = processing.run_job(
                argparse.Namespace(**{**options.to_mapping(), "paths": [options.path]}),
                stdout=None,
            )

        self.assertEqual(result.status, "failed")
        self.assertIn("Output already exists", result.error)

    def test_processing_prevents_detected_language_source_export_overwrite_at_write_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            (Path(tmp) / "clip.source.it.srt").write_text("existing", encoding="utf-8")
            options = JobOptions(
                path=str(media),
                output_dir=tmp,
                translate_to="fr",
                output_source_subtitles=True,
                overwrite=False,
            )
            args = argparse.Namespace(**{**options.to_mapping(), "paths": [options.path]})
            source = {
                "source_key": "key",
                "language": "it",
                "cues": processing.source_cues_from_transcript(
                    {"language": "it", "segments": [{"start": 0.0, "end": 1.0, "text": "ciao"}]},
                    "key",
                    "it",
                ),
            }
            context = processing.ProcessingContext()

            with self.assertRaises(FileExistsError):
                processing.write_source_exports(str(media), source, tmp, args, context)

    def test_processing_log_events_emit_even_when_stdout_is_suppressed(self):
        events = []
        context = processing.ProcessingContext(progress=events.append, stdout=None)

        context.print("File write error: disk full")

        self.assertEqual(events[-1]["state"], "log")
        self.assertEqual(events[-1]["message"], "File write error: disk full")

    def test_asr_only_run_populates_source_cache_for_later_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            cache = Path(tmp) / "cache"
            fake_model = mock.Mock()
            fake_model.transcribe.return_value = {
                "language": "it",
                "segments": [{"start": 0.0, "end": 1.0, "text": "ciao"}],
            }

            fingerprint = {
                "backend": "stable",
                "model": "small",
                "fingerprint_available": True,
                "model_source": "test",
                "model_revision": "revision",
                "model_sha256": "sha256",
                "package": {},
            }

            base = {
                "path": str(media),
                "output_dir": tmp,
                "output_srt": True,
                "language": "it",
                "translation_cache_dir": str(cache),
                "overwrite": True,
            }

            with mock.patch.object(processing, "validate_backend_options"), \
                 mock.patch.object(processing, "load_backend_model", return_value=fake_model), \
                 mock.patch.object(processing, "asr_backend_fingerprint", return_value=fingerprint):
                first = processing.run_job(
                    argparse.Namespace(**{**base, "paths": [str(media)]}),
                    stdout=None,
                )

            with mock.patch.object(processing, "validate_backend_options"), \
                 mock.patch.object(processing, "load_backend_model", side_effect=AssertionError("ASR should use source cache")), \
                 mock.patch.object(processing, "asr_backend_fingerprint", return_value=fingerprint), \
                 mock.patch.object(processing, "TranslationPipeline", FakeTranslationPipeline):
                second = processing.run_job(
                    argparse.Namespace(**{**base, "paths": [str(media)], "translate_to": "fr"}),
                    stdout=None,
                )

        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(fake_model.transcribe.call_count, 1)

    def test_job_runner_reports_failed_result_without_loading_models_for_bad_path(self):
        runner = JobRunner()
        try:
            result = runner.run(JobOptions(path="missing-file.wav"))
        finally:
            runner.close()

        self.assertIsInstance(result, JobResult)
        self.assertEqual(result.status, "failed")
        self.assertIn("Input file does not exist", result.error)

    def test_job_runner_cancel_terminates_owned_worker_and_returns_cancelled(self):
        runner = JobRunner()
        try:
            first_pid = runner.pid
            result = runner.run(JobOptions(path="missing-file.wav"), cancel=lambda: True)
            second_pid = runner.pid
        finally:
            runner.close()

        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(first_pid)
        self.assertIsNone(second_pid)


if __name__ == "__main__":
    unittest.main()

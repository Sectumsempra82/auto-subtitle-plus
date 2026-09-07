import argparse
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import backends
from auto_subtitle_plus import benchmark


class BenchmarkContractTests(unittest.TestCase):
    def test_parse_srt_rejects_malformed_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "bad.srt"
            reference.write_text(
                "1\n00:00:01,000 -> 00:00:02,000\nhello\n\n",
                encoding="utf-8",
            )

            with self.assertRaises(Exception):
                benchmark.parse_srt(str(reference))

    def test_reference_window_uses_full_containment_and_sdh_cleanup(self):
        cues = [
            {"start": 279.8, "end": 280.2, "text": "before boundary"},
            {"start": 282.0, "end": 284.0, "text": "[Cuddy] EVERYTHING'S GOOD."},
            {"start": 399.5, "end": 400.2, "text": "after boundary"},
        ]

        text, selected = benchmark.reference_text_for_clip(cues, 280.0, 120.0)

        self.assertEqual(text, "EVERYTHING'S GOOD.")
        self.assertEqual(
            selected,
            [
                {
                    "start": 2.0,
                    "end": 4.0,
                    "original_start": 282.0,
                    "original_end": 284.0,
                    "text": "EVERYTHING'S GOOD.",
                }
            ],
        )

    def test_reference_window_requires_valid_duration(self):
        with self.assertRaises(ValueError):
            benchmark.reference_text_for_clip(
                [{"start": 1.0, "end": 2.0, "text": "hello"}],
                0.0,
                None,
            )

        with self.assertRaises(ValueError):
            benchmark.reference_text_for_clip(
                [{"start": 1.0, "end": 2.0, "text": "hello"}],
                0.0,
                0.0,
            )

    def test_validate_segments_rejects_nonfinite_and_equal_timestamps(self):
        result = benchmark.validate_segments(
            [
                {"start": math.nan, "end": 1.0, "text": "nan start"},
                {"start": 2.0, "end": 2.0, "text": "zero duration"},
                {"start": 3.0, "end": math.inf, "text": "inf end"},
            ],
            duration=120.0,
            tolerance=0.5,
        )

        self.assertFalse(result["timestamp_valid"])
        reasons = " ".join(item["reason"] for item in result["invalid_segments"])
        self.assertIn("finite", reasons)
        self.assertIn("end", reasons)

    def test_benchmark_model_matches_cli_transcribe_defaults_and_times_inference_only(self):
        fake_segments = [{"start": 0.1, "end": 1.2, "text": "hello world"}]

        class FakeModel:
            def transcribe(self, audio_path, **kwargs):
                self.kwargs = kwargs
                return {"segments": fake_segments}

        fake_model = FakeModel()
        clock = iter([10.0, 13.0, 13.5, 15.5, 16.25])
        args = argparse.Namespace(
            backend="stable",
            model="small",
            device="cuda",
            compute_type="auto",
            inference_batch_size=1,
            vad=False,
            language="en",
            verbose=False,
            timestamp_tolerance=0.5,
            enhance_consistency=False,
            word_timestamps=False,
        )

        with mock.patch.object(benchmark, "load_backend_model", return_value=fake_model) as load_backend_model, \
             mock.patch.object(benchmark.time, "perf_counter", side_effect=lambda: next(clock)):
            result = benchmark.benchmark_model(
                "turbo",
                "clip.wav",
                "hello world",
                100.0,
                args,
            )

        self.assertEqual(load_backend_model.call_args.args[0].backend, "stable")
        self.assertEqual(load_backend_model.call_args.args[0].model, "turbo")
        self.assertEqual(
            fake_model.kwargs,
            {
                "language": "en",
                "verbose": False,
                "condition_on_previous_text": False,
                "word_timestamps": False,
            },
        )
        self.assertEqual(result["model_load_wall_time_seconds"], 3.0)
        self.assertEqual(result["inference_wall_time_seconds"], 2.0)
        self.assertEqual(result["total_wall_time_seconds"], 6.25)
        self.assertEqual(result["real_time_factor"], 0.02)
        self.assertEqual(result["backend"], "stable")
        self.assertNotIn("load_time_seconds", result)
        self.assertNotIn("transcribe_time_seconds", result)

    def test_benchmark_model_preserves_backend_timestamp_adjustment_metadata(self):
        class FakeModel:
            def transcribe(self, *_args, **_kwargs):
                return {
                    "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
                    "timestamp_adjustments": 2,
                    "timestamp_warning": "Adjusted 2 timestamp value(s) to fit media duration.",
                }

        args = argparse.Namespace(
            backend="faster",
            model="small",
            device="cpu",
            compute_type="int8",
            inference_batch_size=1,
            vad=False,
            language="en",
            verbose=False,
            timestamp_tolerance=0.5,
            enhance_consistency=False,
            word_timestamps=False,
        )

        with mock.patch.object(benchmark, "load_backend_model", return_value=FakeModel()), \
             mock.patch.object(benchmark.time, "perf_counter", side_effect=[1.0, 2.0, 3.0, 4.0, 5.0]):
            result = benchmark.benchmark_model(
                "small",
                "clip.wav",
                "hello",
                10.0,
                args,
            )

        self.assertEqual(result["timestamp_adjustments"], 2)
        self.assertIn("duration", result["timestamp_warning"].lower())

    def test_benchmark_failed_distil_alias_reports_requested_effective_and_backend(self):
        args = argparse.Namespace(
            backend="faster",
            model=backends.DISTIL_LARGE_V35_MODEL,
            device="cpu",
            compute_type="int8",
            inference_batch_size=1,
            vad=False,
            language="en",
            verbose=False,
            timestamp_tolerance=0.5,
            enhance_consistency=False,
            word_timestamps=False,
        )

        with mock.patch.object(benchmark, "load_backend_model", side_effect=RuntimeError("load failed")), \
             mock.patch.object(benchmark.time, "perf_counter", side_effect=[1.0, 2.0]):
            result = benchmark.benchmark_model(
                backends.DISTIL_LARGE_V35_MODEL,
                "clip.wav",
                "hello",
                10.0,
                args,
                requested_model_name=backends.DISTIL_LARGE_V35_ALIAS,
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["model"], backends.DISTIL_LARGE_V35_ALIAS)
        self.assertEqual(result["effective_model"], backends.DISTIL_LARGE_V35_MODEL)
        self.assertEqual(result["backend"], "faster")

    def test_main_writes_incremental_report_and_continues_after_model_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            reference = Path(tmp) / "reference.srt"
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"fake")
            reference.write_text(
                "1\n00:04:42,000 --> 00:04:44,000\n[Cuddy] EVERYTHING'S GOOD.\n\n",
                encoding="utf-8",
            )
            calls = []

            def fake_benchmark_model(model_name, *_args):
                calls.append(model_name)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(
                    [item["model"] for item in report["models"]],
                    calls[:-1],
                    "report should be written before each next risky model run",
                )
                if model_name == "bad":
                    return {
                        "model": model_name,
                        "status": "error",
                        "error": "CUDA out of memory",
                        "oom": True,
                    }
                return {
                    "model": model_name,
                    "status": "ok",
                    "wer": 0.0,
                    "model_load_wall_time_seconds": 1.0,
                    "inference_wall_time_seconds": 2.0,
                    "total_wall_time_seconds": 3.0,
                    "real_time_factor": 0.02,
                    "segment_count": 1,
                    "segments": [{"start": 2.0, "end": 4.0, "text": "Everything's good."}],
                    "timestamp_valid": True,
                    "nonempty_segments": True,
                    "invalid_segments": [],
                }

            with mock.patch.object(
                benchmark,
                "parse_args",
                return_value=argparse.Namespace(
                    audio=str(audio),
                    reference_srt=str(reference),
                    output_json=str(output),
                    models=["bad", "good"],
                    backend="stable",
                    device="cuda",
                    compute_type="auto",
                    inference_batch_size=1,
                    vad=False,
                    language="en",
                    reference_offset=280.0,
                    timestamp_tolerance=0.5,
                    verbose=False,
                ),
            ), mock.patch.object(benchmark, "probe_duration", return_value=120.0), \
                 mock.patch.object(benchmark, "benchmark_model", side_effect=fake_benchmark_model):
                exit_code = benchmark.main()

            self.assertEqual(exit_code, 1)
            self.assertEqual(calls, ["bad", "good"])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual([item["model"] for item in report["models"]], ["bad", "good"])
            self.assertEqual(report["models"][0]["status"], "error")
            self.assertEqual(report["models"][1]["status"], "ok")
            self.assertEqual(report["reference_segments"][0]["text"], "EVERYTHING'S GOOD.")


if __name__ == "__main__":
    unittest.main()

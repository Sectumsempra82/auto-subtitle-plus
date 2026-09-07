import unittest
from unittest import mock

from auto_subtitle_plus import transcription_worker


class TranscriptionWorkerContractTests(unittest.TestCase):
    def worker_args(self, **overrides):
        values = {
            "backend": "stable",
            "model": "small",
            "language": "it",
            "device": "cpu",
            "compute_type": "auto",
            "inference_batch_size": 1,
            "vad": False,
            "verbose": False,
            "enhance_consistency": False,
            "word_timestamps": False,
            "offline": True,
        }
        values.update(overrides)
        return values

    def test_worker_passes_offline_args_transcribes_serializes_cues_and_closes_model(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {
            "language": "it",
            "segments": [{"start": 0.0, "end": 1.0, "text": "ciao"}],
        }

        with mock.patch.object(transcription_worker, "load_backend_model", return_value=fake_model) as load_backend_model:
            result = transcription_worker.transcribe_source_worker(
                "input.wav",
                "audio.wav",
                self.worker_args(),
                "source-key",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["input_path"], "input.wav")
        self.assertEqual(result["source_key"], "source-key")
        self.assertEqual(result["language"], "it")
        self.assertEqual(result["cues"][0]["text"], "ciao")
        self.assertEqual(result["cues"][0]["language"], "it")
        self.assertIs(load_backend_model.call_args.args[0].offline, True)
        fake_model.transcribe.assert_called_once_with(
            "audio.wav",
            language="it",
            verbose=False,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        fake_model.close.assert_called_once()

    def test_worker_returns_formatted_error_and_closes_model_after_transcribe_failure(self):
        fake_model = mock.Mock()
        fake_model.transcribe.side_effect = RuntimeError("cublas64_12.dll missing")

        with mock.patch.object(transcription_worker, "load_backend_model", return_value=fake_model):
            result = transcription_worker.transcribe_source_worker(
                "input.wav",
                "audio.wav",
                self.worker_args(),
                "source-key",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["input_path"], "input.wav")
        self.assertIn("CUDA runtime is missing", result["error"])
        fake_model.close.assert_called_once()

    def test_worker_returns_load_error_without_close_when_model_never_created(self):
        with mock.patch.object(transcription_worker, "load_backend_model", side_effect=RuntimeError("offline mode forbids ASR downloads")):
            result = transcription_worker.transcribe_source_worker(
                "input.wav",
                "audio.wav",
                self.worker_args(),
                "source-key",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["input_path"], "input.wav")
        self.assertIn("offline mode forbids ASR downloads", result["error"])


if __name__ == "__main__":
    unittest.main()

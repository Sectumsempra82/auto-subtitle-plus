import argparse
import hashlib
import io
import os
import inspect
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import cli, utils


class FasterBackendContractTests(unittest.TestCase):
    def import_backends(self):
        from auto_subtitle_plus import backends

        return backends

    def test_missing_faster_dependency_is_lazy_and_actionable(self):
        backends = self.import_backends()
        args = argparse.Namespace(
            backend="faster",
            model="small",
            device="cpu",
            compute_type="auto",
            inference_batch_size=1,
            vad=False,
        )

        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            with self.assertRaisesRegex(RuntimeError, r"\.\[faster\]"):
                backends.load_backend_model(args)

    def test_stable_backend_preserves_existing_load_and_transcribe_defaults(self):
        backends = self.import_backends()
        stable_model = mock.Mock()
        stable_model.transcribe.return_value = {"segments": [{"start": 0, "end": 1, "text": "hi"}]}
        fake_stable_whisper = types.SimpleNamespace(load_model=mock.Mock(return_value=stable_model))

        with mock.patch.object(backends.stable_whisper, "module", fake_stable_whisper):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="stable",
                    model="small",
                    device="cuda",
                    compute_type="auto",
                    inference_batch_size=1,
                    vad=False,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language="en",
                verbose=False,
                condition_on_previous_text=False,
                word_timestamps=False,
            )

        fake_stable_whisper.load_model.assert_called_once_with("small", device="cuda")
        stable_model.transcribe.assert_called_once_with(
            "clip.wav",
            language="en",
            verbose=False,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        self.assertEqual(utils.normalize_segments(result), [{"start": 0, "end": 1, "text": "hi"}])

    def test_stable_offline_rejects_corrupt_cached_model_before_loading(self):
        backends = self.import_backends()
        digest = hashlib.sha256(b"expected model bytes").hexdigest()
        original_expanduser = os.path.expanduser

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / ".cache" / "whisper"
            cache_dir.mkdir(parents=True)
            cached_model = cache_dir / "tiny.pt"
            cached_model.write_bytes(b"corrupt")
            fake_whisper = types.SimpleNamespace(_MODELS={"tiny": f"https://example.test/{digest}/tiny.pt"})
            fake_stable_whisper = types.SimpleNamespace(load_model=mock.Mock(side_effect=AssertionError("corrupt offline model loaded")))

            with mock.patch.object(backends.whisper, "module", fake_whisper), \
                 mock.patch.object(backends.stable_whisper, "module", fake_stable_whisper), \
                 mock.patch.object(backends.os.path, "expanduser", side_effect=lambda path: str(Path(tmp)) if path == "~" else original_expanduser(path)), \
                 mock.patch.dict(backends.os.environ, {"XDG_CACHE_HOME": str(Path(tmp) / ".cache")}):
                with self.assertRaisesRegex(RuntimeError, "SHA256 verification|offline mode forbids redownload"):
                    backends.load_backend_model(
                        argparse.Namespace(
                            backend="stable",
                            model="tiny",
                            device="cpu",
                            compute_type="auto",
                            inference_batch_size=1,
                            vad=False,
                            offline=True,
                        )
                    )

    def test_faster_offline_uses_existing_local_converted_path_without_download_model(self):
        backends = self.import_backends()
        calls = {}

        class FakeWhisperModel:
            def __init__(self, model_name, **kwargs):
                calls["init"] = (model_name, kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            local_model = Path(tmp) / "converted-ct2"
            local_model.mkdir()
            (local_model / "model.bin").write_bytes(b"ct2")

            fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
            fake_utils = types.SimpleNamespace(download_model=mock.Mock(side_effect=AssertionError("download_model called for local path")))

            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module, "faster_whisper.utils": fake_utils}):
                model = backends.load_backend_model(
                    argparse.Namespace(
                        backend="faster",
                        model=str(local_model),
                        device="cpu",
                        compute_type="int8",
                        inference_batch_size=1,
                        vad=False,
                        offline=True,
                    )
                )

        self.assertIsInstance(model, backends.FasterBackend)
        self.assertEqual(
            calls["init"],
            (str(local_model), {"device": "cpu", "compute_type": "int8", "local_files_only": True}),
        )

    def test_faster_turbo_alias_fingerprint_uses_registry_repo_and_snapshot_revision(self):
        backends = self.import_backends()
        repo_id = "Systran/faster-whisper-large-v3-turbo"
        snapshot_revision = "feedface1234567890abcdef"
        calls = []

        with tempfile.TemporaryDirectory() as tmp:
            model_bin = Path(tmp) / "models--Systran--faster-whisper-large-v3-turbo" / "snapshots" / snapshot_revision / "model.bin"
            model_bin.parent.mkdir(parents=True)
            model_bin.write_bytes(b"turbo canonical model")

            fake_faster_whisper = types.ModuleType("faster_whisper")
            fake_faster_utils = types.ModuleType("faster_whisper.utils")
            fake_faster_utils._MODELS = {
                "turbo": repo_id,
                "large-v3-turbo": repo_id,
            }
            fake_faster_whisper.utils = fake_faster_utils

            fake_hub = types.ModuleType("huggingface_hub")

            def fake_try_to_load_from_cache(requested_repo_id, filename):
                calls.append((requested_repo_id, filename))
                return str(model_bin)

            fake_hub.try_to_load_from_cache = fake_try_to_load_from_cache

            with mock.patch.dict(
                sys.modules,
                {
                    "faster_whisper": fake_faster_whisper,
                    "faster_whisper.utils": fake_faster_utils,
                    "huggingface_hub": fake_hub,
                },
            ):
                fingerprint = backends.faster_model_fingerprint("turbo")

        self.assertEqual(calls, [(repo_id, "model.bin")])
        self.assertTrue(fingerprint["fingerprint_available"])
        self.assertEqual(fingerprint["repo_id"], repo_id)
        self.assertEqual(fingerprint["model_revision"], snapshot_revision)
        self.assertEqual(fingerprint["model_sha256"], hashlib.sha256(b"turbo canonical model").hexdigest())

    def test_faster_direct_materializes_generator_and_forwards_arguments(self):
        backends = self.import_backends()
        calls = {}

        class FakeWhisperModel:
            def __init__(self, model_name, device, compute_type):
                calls["init"] = (model_name, device, compute_type)

            def transcribe(self, audio_path, **kwargs):
                calls["transcribe"] = (audio_path, kwargs)
                return iter([
                    types.SimpleNamespace(start=0.0, end=1.0, text="hello"),
                    types.SimpleNamespace(start=1.2, end=2.0, text="world"),
                ]), types.SimpleNamespace(duration=2.0, language="en")

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="Systran/faster-whisper-small",
                    device="cuda",
                    compute_type="float16",
                    inference_batch_size=1,
                    vad=True,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language="en",
                verbose=False,
                condition_on_previous_text=True,
                word_timestamps=True,
            )

        self.assertEqual(calls["init"], ("Systran/faster-whisper-small", "cuda", "float16"))
        self.assertEqual(calls["transcribe"][0], "clip.wav")
        self.assertEqual(calls["transcribe"][1]["language"], "en")
        self.assertIs(calls["transcribe"][1]["vad_filter"], True)
        self.assertIs(calls["transcribe"][1]["word_timestamps"], True)
        self.assertEqual(
            utils.normalize_segments(result),
            [{"start": 0.0, "end": 1.0, "text": "hello"}, {"start": 1.2, "end": 2.0, "text": "world"}],
        )

    def test_faster_batched_pipeline_only_when_batch_size_above_one(self):
        backends = self.import_backends()
        calls = {}

        class FakeWhisperModel:
            def __init__(self, *args, **kwargs):
                calls["model_init"] = (args, kwargs)

            def transcribe(self, *_args, **_kwargs):
                raise AssertionError("base model should not transcribe when batching")

        class FakePipeline:
            def __init__(self, model):
                calls["pipeline_model"] = model

            def transcribe(self, audio_path, **kwargs):
                calls["pipeline_transcribe"] = (audio_path, kwargs)
                return iter([types.SimpleNamespace(start=0.0, end=1.0, text="batched")]), types.SimpleNamespace(duration=1.0)

        fake_module = types.SimpleNamespace(
            WhisperModel=FakeWhisperModel,
            BatchedInferencePipeline=FakePipeline,
        )
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="small",
                    device="cpu",
                    compute_type="int8",
                    inference_batch_size=4,
                    vad=True,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language=None,
                verbose=True,
                condition_on_previous_text=False,
                word_timestamps=False,
            )

        self.assertEqual(calls["pipeline_transcribe"][0], "clip.wav")
        self.assertEqual(calls["pipeline_transcribe"][1]["batch_size"], 4)
        self.assertIs(calls["pipeline_transcribe"][1]["vad_filter"], True)
        self.assertEqual(utils.normalize_segments(result), [{"start": 0.0, "end": 1.0, "text": "batched"}])

    def test_faster_direct_bounds_padded_tail_to_original_duration(self):
        backends = self.import_backends()

        class FakeWhisperModel:
            def __init__(self, *_args, **_kwargs):
                pass

            def transcribe(self, *_args, **_kwargs):
                return iter([
                    types.SimpleNamespace(start=10.0, end=11.0, text="inside"),
                    types.SimpleNamespace(
                        start=118.5,
                        end=121.0,
                        text="tail",
                        words=[
                            types.SimpleNamespace(start=118.5, end=119.0, word="tail"),
                            types.SimpleNamespace(start=119.8, end=121.0, word="word"),
                        ],
                    ),
                    types.SimpleNamespace(start=120.5, end=121.0, text="outside"),
                    types.SimpleNamespace(start=119.9, end=119.9, text="zero"),
                ]), types.SimpleNamespace(duration=120.0, duration_after_vad=12.0)

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="small",
                    device="cpu",
                    compute_type="int8",
                    inference_batch_size=1,
                    vad=False,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language="en",
                verbose=False,
                condition_on_previous_text=False,
                word_timestamps=True,
            )

        self.assertEqual(
            utils.normalize_segments(result),
            [
                {"start": 10.0, "end": 11.0, "text": "inside"},
                {
                    "start": 118.5,
                    "end": 120.0,
                    "text": "tail",
                    "words": [
                        {"start": 118.5, "end": 119.0, "word": "tail"},
                        {"start": 119.8, "end": 120.0, "word": "word"},
                    ],
                },
            ],
        )
        self.assertGreater(result["timestamp_adjustments"], 0)
        self.assertIn("duration", result["timestamp_warning"].lower())

    def test_faster_batched_bounds_padded_tail_to_original_duration(self):
        backends = self.import_backends()

        class FakeWhisperModel:
            def __init__(self, *_args, **_kwargs):
                pass

        class FakePipeline:
            def __init__(self, model):
                self.model = model

            def transcribe(self, *_args, **_kwargs):
                return iter([
                    types.SimpleNamespace(start=119.0, end=122.0, text="batched tail"),
                ]), types.SimpleNamespace(duration=120.0, duration_after_vad=3.0)

        fake_module = types.SimpleNamespace(
            WhisperModel=FakeWhisperModel,
            BatchedInferencePipeline=FakePipeline,
        )
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="small",
                    device="cpu",
                    compute_type="int8",
                    inference_batch_size=4,
                    vad=True,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language="en",
                verbose=False,
                condition_on_previous_text=False,
                word_timestamps=False,
            )

        self.assertEqual(
            utils.normalize_segments(result),
            [{"start": 119.0, "end": 120.0, "text": "batched tail"}],
        )
        self.assertGreater(result["timestamp_adjustments"], 0)
        self.assertIn("duration", result["timestamp_warning"].lower())

    def test_faster_drops_all_outside_duration_segments(self):
        backends = self.import_backends()

        class FakeWhisperModel:
            def __init__(self, *_args, **_kwargs):
                pass

            def transcribe(self, *_args, **_kwargs):
                return iter([
                    types.SimpleNamespace(start=120.1, end=121.0, text="outside"),
                    types.SimpleNamespace(start=130.0, end=131.0, text="outside later"),
                ]), types.SimpleNamespace(duration=120.0)

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="small",
                    device="cpu",
                    compute_type="int8",
                    inference_batch_size=1,
                    vad=False,
                )
            )
            result = model.transcribe(
                "clip.wav",
                language="en",
                verbose=False,
                condition_on_previous_text=False,
                word_timestamps=False,
            )

        self.assertEqual(utils.normalize_segments(result), [])
        self.assertGreater(result["timestamp_adjustments"], 0)
        self.assertIn("dropped", result["timestamp_warning"].lower())

    def test_faster_rejects_nonfinite_segment_timestamp(self):
        backends = self.import_backends()

        class FakeWhisperModel:
            def __init__(self, *_args, **_kwargs):
                pass

            def transcribe(self, *_args, **_kwargs):
                return iter([
                    types.SimpleNamespace(start=0.0, end=float("nan"), text="bad"),
                ]), types.SimpleNamespace(duration=120.0)

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            model = backends.load_backend_model(
                argparse.Namespace(
                    backend="faster",
                    model="small",
                    device="cpu",
                    compute_type="int8",
                    inference_batch_size=1,
                    vad=False,
                )
            )
            with self.assertRaisesRegex(ValueError, "finite"):
                model.transcribe(
                    "clip.wav",
                    language="en",
                    verbose=False,
                    condition_on_previous_text=False,
                    word_timestamps=False,
                )

    def test_faster_batch_size_above_one_requires_vad(self):
        backends = self.import_backends()
        parser = argparse.ArgumentParser()

        with self.assertRaises(SystemExit):
            backends.validate_backend_options(
                argparse.Namespace(
                    backend="faster",
                    models=["small"],
                    compute_type="auto",
                    inference_batch_size=2,
                    vad=False,
                    enhance_consistency=False,
                ),
                parser,
            )

    def test_faster_batch_size_above_one_rejects_enhance_consistency(self):
        backends = self.import_backends()
        parser = argparse.ArgumentParser()

        with self.assertRaises(SystemExit):
            backends.validate_backend_options(
                argparse.Namespace(
                    backend="faster",
                    models=["small"],
                    compute_type="auto",
                    inference_batch_size=2,
                    vad=True,
                    enhance_consistency=True,
                ),
                parser,
            )

    def test_faster_single_inference_accepts_no_vad_and_enhance_consistency(self):
        backends = self.import_backends()
        parser = argparse.ArgumentParser()

        backends.validate_backend_options(
            argparse.Namespace(
                backend="faster",
                models=["small"],
                compute_type="auto",
                inference_batch_size=1,
                vad=False,
                enhance_consistency=True,
            ),
            parser,
        )

    def test_cli_rejects_invalid_backend_options_before_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            output = Path(tmp) / "out"

            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--backend",
                    "stable",
                    "--compute-type",
                    "float16",
                    "--output-dir",
                    str(output),
                ],
            ):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main()

        self.assertNotEqual(ctx.exception.code, 0)

    def test_cli_continues_files_after_transcribe_error_but_returns_nonzero(self):
        backends = self.import_backends()
        with tempfile.TemporaryDirectory() as tmp:
            first = str(Path(tmp) / "first.wav")
            second = str(Path(tmp) / "second.wav")
            Path(first).write_bytes(b"fake")
            Path(second).write_bytes(b"fake")
            fake_model = mock.Mock()
            fake_model.transcribe.side_effect = [
                RuntimeError("bad file"),
                {"segments": [{"start": 0.0, "end": 1.0, "text": "good file"}]},
            ]
            args = [
                "auto_subtitle_plus",
                first,
                second,
                "--output-srt",
                "--output-dir",
                tmp,
            ]
            fake_whisper = types.SimpleNamespace(available_models=mock.Mock(return_value=["small", "turbo"]))
            with mock.patch.object(sys, "argv", args), \
                 mock.patch.object(backends.whisper, "module", fake_whisper), \
                 mock.patch.object(cli.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(cli, "is_audio", return_value=True), \
                 mock.patch.object(cli, "default_device", return_value="cpu"), \
                 mock.patch.object(cli, "load_backend_model", return_value=fake_model):
                exit_code = cli.main()

            self.assertNotEqual(exit_code, 0)
            self.assertFalse((Path(tmp) / "first.srt").exists())
            self.assertTrue((Path(tmp) / "second.srt").exists())

    def test_cli_continues_after_video_extraction_failure_and_returns_nonzero(self):
        backends = self.import_backends()

        class FakePool:
            def __init__(self, workers):
                self.workers = workers

            def __enter__(self):
                return self

            def __exit__(self, *_exc_info):
                return False

            def starmap(self, function, tasks):
                results = []
                for task in tasks:
                    results.append(function(*task))
                return results

        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "good file"}]
        }

        with tempfile.TemporaryDirectory() as tmp:
            bad_video = str(Path(tmp) / "bad.mp4")
            good_video = str(Path(tmp) / "good.mp4")
            audio = str(Path(tmp) / "audio.wav")
            Path(bad_video).write_bytes(b"bad")
            Path(good_video).write_bytes(b"good")
            Path(audio).write_bytes(b"audio")

            def fake_extract(input_path, output_path):
                if input_path == bad_video:
                    raise RuntimeError("extract failed")
                Path(output_path).write_bytes(b"extracted")

            fake_whisper = types.SimpleNamespace(available_models=mock.Mock(return_value=["small", "turbo"]))
            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    bad_video,
                    good_video,
                    audio,
                    "--output-srt",
                    "--output-dir",
                    tmp,
                    "--extract-workers",
                    "1",
                ],
            ), mock.patch.object(backends.whisper, "module", fake_whisper), \
                 mock.patch.object(cli.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(cli.multiprocessing, "Pool", FakePool), \
                 mock.patch.object(cli, "ffmpeg_extract_audio", side_effect=fake_extract), \
                 mock.patch.object(cli, "default_device", return_value="cpu"), \
                 mock.patch.object(cli, "load_backend_model", return_value=fake_model):
                exit_code = cli.main()

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(fake_model.transcribe.call_count, 2)
        transcribed_paths = [call.args[0] for call in fake_model.transcribe.call_args_list]
        self.assertFalse(any(Path(path).name == "bad.mp3" for path in transcribed_paths))
        self.assertTrue(any(Path(path).name == "good.mp3" for path in transcribed_paths))
        self.assertIn(audio, transcribed_paths)

    def test_get_audio_preserves_input_order_after_video_extraction(self):
        class FakePool:
            def __init__(self, workers):
                self.workers = workers

            def __enter__(self):
                return self

            def __exit__(self, *_exc_info):
                return False

            def starmap(self, function, tasks):
                return [function(*task) for task in tasks]

        with tempfile.TemporaryDirectory() as tmp:
            first_video = str(Path(tmp) / "first.mp4")
            audio = str(Path(tmp) / "middle.wav")
            second_video = str(Path(tmp) / "second.mp4")
            for path in (first_video, audio, second_video):
                Path(path).write_bytes(b"fake")

            with mock.patch.object(cli.multiprocessing, "Pool", FakePool), \
                 mock.patch.object(cli, "ffmpeg_extract_audio"):
                audio_map, failures = cli.get_audio(
                    [first_video, audio, second_video],
                    save_audio=True,
                    output_dir=tmp,
                    num_workers=1,
                )

        self.assertEqual(failures, 0)
        self.assertEqual(list(audio_map.keys()), [first_video, audio, second_video])
        self.assertEqual(Path(audio_map[first_video]).name, "first.mp3")
        self.assertEqual(audio_map[audio], audio)
        self.assertEqual(Path(audio_map[second_video]).name, "second.mp3")

    def test_cli_audio_extraction_worker_is_top_level_for_windows_spawn(self):
        worker = cli.extract_audio_worker
        self.assertTrue(inspect.isfunction(worker))
        self.assertEqual(worker.__module__, cli.__name__)
        self.assertNotIn("<locals>", worker.__qualname__)

    def test_cli_returns_nonzero_when_subtitle_write_fails(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "good file"}]
        }

        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"fake")
            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(media),
                    "--output-srt",
                    "--output-dir",
                    tmp,
                ],
            ), mock.patch.object(cli.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(cli, "is_audio", return_value=True), \
                 mock.patch.object(cli, "load_backend_model", return_value=fake_model), \
                 mock.patch.object(cli, "write_subtitle", side_effect=RuntimeError("disk full")):
                exit_code = cli.main()

        self.assertNotEqual(exit_code, 0)

    def test_cli_returns_nonzero_when_video_creation_fails(self):
        fake_model = mock.Mock()
        fake_model.transcribe.return_value = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "good file"}]
        }

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            audio = Path(tmp) / "clip.mp3"
            video.write_bytes(b"fake")
            audio.write_bytes(b"fake")
            with mock.patch.object(
                sys,
                "argv",
                [
                    "auto_subtitle_plus",
                    str(video),
                    "--output-srt",
                    "--output-video",
                    "--output-dir",
                    tmp,
                ],
            ), mock.patch.object(cli.glob, "glob", side_effect=lambda value: [value]), \
                 mock.patch.object(cli, "get_audio", return_value=({str(video): str(audio)}, 0)), \
                 mock.patch.object(cli, "is_audio", return_value=False), \
                 mock.patch.object(cli, "load_backend_model", return_value=fake_model), \
                 mock.patch.object(cli, "run_ffmpeg_with_progress", side_effect=RuntimeError("ffmpeg failed")):
                exit_code = cli.main()

        self.assertNotEqual(exit_code, 0)

    def test_existing_srt_vtt_txt_translation_and_bilingual_serialization_still_work(self):
        transcript = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "FR:Hello", "source_text": "Hello"},
                {"start": 1.0, "end": 2.0, "text": "FR:World", "source_text": "World"},
            ]
        }

        srt_output = io.StringIO()
        utils.write_subtitle(
            transcript,
            srt_output,
            subtitle_format="srt",
            translate_to="fr",
            translate_off=False,
            bilingual=True,
            batch_size=2,
            max_workers=1,
        )
        vtt_output = io.StringIO()
        utils.write_subtitle(
            transcript,
            vtt_output,
            subtitle_format="vtt",
            translate_off=True,
        )
        txt_output = io.StringIO()
        utils.write_txt(transcript, txt_output)

        self.assertIn("Hello\nFR:Hello", srt_output.getvalue())
        self.assertIn("WEBVTT", vtt_output.getvalue())
        self.assertIn("00:00:00.000 --> 00:00:01.000", vtt_output.getvalue())
        self.assertEqual(txt_output.getvalue().strip().splitlines(), ["FR:Hello", "FR:World"])

    def test_timestamp_formatting_uses_canonical_hours_when_requested(self):
        self.assertEqual(
            utils.format_timestamp(
                1.25,
                always_include_hours=True,
                decimal_marker=",",
            ),
            "00:00:01,250",
        )
        self.assertEqual(
            utils.format_timestamp(
                1.25,
                always_include_hours=True,
                decimal_marker=".",
            ),
            "00:00:01.250",
        )
        self.assertEqual(
            utils.format_timestamp(
                1.25,
                always_include_hours=False,
                decimal_marker=",",
            ),
            "00:01,250",
        )


if __name__ == "__main__":
    unittest.main()

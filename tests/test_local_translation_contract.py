import hashlib
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import local_translation, model_manager


class FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeDownloadResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, _chunk_size):
        yield self.content


class FakeLlamaSession:
    def __init__(self, completion_payload):
        self.completion_payload = completion_payload
        self.posts = []

    def post(self, url, json, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/tokenize"):
            return FakeHttpResponse({"tokens": [1, 2, 3]})
        return FakeHttpResponse(self.completion_payload)

    def close(self):
        pass


class FakeTokenizer:
    def __init__(self):
        self.src_lang = None

    def encode(self, text, truncation=False):
        return [ord(char) for char in text]

    def convert_ids_to_tokens(self, ids):
        return [str(item) for item in ids]

    def convert_tokens_to_ids(self, tokens):
        return [int(item) for item in tokens]

    def decode(self, ids, skip_special_tokens=True):
        return "".join(chr(item) for item in ids)

    def get_lang_id(self, language):
        return 1000 + len(language)


class FakeCT2Model:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []
        self.unloaded = False

    def translate_batch(self, batch, **kwargs):
        self.calls.append({"batch": batch, "kwargs": kwargs})
        return [types.SimpleNamespace(hypotheses=[[str(ord(char)) for char in text]]) for text in self.outputs]

    def unload_model(self):
        self.unloaded = True


class MemoryPressureCT2Model:
    def __init__(self):
        self.calls = []

    def translate_batch(self, batch, **kwargs):
        self.calls.append({"batch": batch, "kwargs": kwargs})
        if len(batch) > 1:
            raise RuntimeError("CUDA out of memory")
        text = "".join(chr(int(token)) for token in batch[0])
        return [types.SimpleNamespace(hypotheses=[[str(ord(char)) for char in f"ok:{text}"]])]

    def unload_model(self):
        pass


class AlwaysOOMCT2Model:
    def __init__(self):
        self.unloaded = False

    def translate_batch(self, batch, **kwargs):
        raise RuntimeError("CUDA out of memory")

    def unload_model(self):
        self.unloaded = True


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class BlockingLlamaSession(FakeLlamaSession):
    def __init__(self):
        super().__init__(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"translations": [{"id": "0", "text": "Bonjour"}]})},
                    }
                ]
            }
        )

    def post(self, url, json, timeout):
        if url.endswith("/tokenize"):
            return FakeHttpResponse({"tokens": [1, 2, 3]})
        time.sleep(0.5)
        return super().post(url, json, timeout)


class LocalTranslationContractTests(unittest.TestCase):
    def tearDown(self):
        local_translation.close_translation_engines()

    def runtime_receipt(self, directory, relative_name, content):
        path = directory / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        (directory / "receipt.json").write_text(
            json.dumps(
                [
                    {
                        "name": relative_name.as_posix(),
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "git_blob": "",
                    }
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_ensure_llama_runtime_offline_rejects_missing_runtime_without_requests_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.dict(sys.modules, {"requests": None}):
                with self.assertRaisesRegex(RuntimeError, "offline mode forbids downloads"):
                    local_translation.ensure_llama_runtime("cpu", offline=True, cache_dir=tmp)

    def test_ensure_llama_runtime_offline_uses_verified_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = (
                local_translation.cache_root(tmp)
                / "runtimes"
                / f"llama-{local_translation.LLAMA_RELEASE}-cpu"
            )
            executable = self.runtime_receipt(directory, Path("bin") / "llama-server.exe", b"exe")

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.dict(sys.modules, {"requests": None}):
                result = local_translation.ensure_llama_runtime("cpu", offline=True, cache_dir=tmp)

        self.assertEqual(result, executable)

    def test_ensure_llama_runtime_prefers_verified_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundled = bundle / "runtimes" / f"llama-{local_translation.LLAMA_RELEASE}-cpu"
            executable = self.runtime_receipt(bundled, Path("bin") / "llama-server.exe", b"exe")

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.object(local_translation.portable.sys, "frozen", True, create=True), \
                 mock.patch.object(local_translation.portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.dict(sys.modules, {"requests": None}):
                result = local_translation.ensure_llama_runtime("cpu", offline=True, cache_dir=Path(tmp) / "cache")

        self.assertEqual(result, executable)

    def test_ensure_llama_runtime_rejects_corrupt_bundled_runtime_in_offline_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundled = bundle / "runtimes" / f"llama-{local_translation.LLAMA_RELEASE}-cpu"
            self.runtime_receipt(bundled, Path("bin") / "llama-server.exe", b"exe")
            (bundled / "bin" / "llama-server.exe").write_bytes(b"bad")

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.object(local_translation.portable.sys, "frozen", True, create=True), \
                 mock.patch.object(local_translation.portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.dict(sys.modules, {"requests": None}):
                with self.assertRaisesRegex(RuntimeError, "offline mode forbids downloads"):
                    local_translation.ensure_llama_runtime("cpu", offline=True, cache_dir=Path(tmp) / "cache")

    def test_ensure_llama_runtime_offline_rejects_corrupt_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = (
                local_translation.cache_root(tmp)
                / "runtimes"
                / f"llama-{local_translation.LLAMA_RELEASE}-cpu"
            )
            self.runtime_receipt(directory, Path("bin") / "llama-server.exe", b"exe")
            (directory / "bin" / "llama-server.exe").write_bytes(b"bad")

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.dict(sys.modules, {"requests": None}):
                with self.assertRaisesRegex(RuntimeError, "offline mode forbids downloads"):
                    local_translation.ensure_llama_runtime("cpu", offline=True, cache_dir=tmp)

    def test_runtime_receipt_rejects_paths_outside_runtime_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "runtime"
            directory.mkdir()
            outside = Path(tmp) / "outside.exe"
            outside.write_bytes(b"exe")
            (directory / "receipt.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "../outside.exe",
                            "size": 3,
                            "sha256": hashlib.sha256(b"exe").hexdigest(),
                            "git_blob": "",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            self.assertFalse(local_translation._runtime_valid(directory))

    def test_runtime_receipt_rejects_malformed_or_empty_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "runtime"
            directory.mkdir()
            (directory / "receipt.json").write_text("{bad", encoding="utf-8")
            self.assertFalse(local_translation._runtime_valid(directory))

            (directory / "receipt.json").write_text("[]", encoding="utf-8")
            self.assertFalse(local_translation._runtime_valid(directory))

            (directory / "receipt.json").write_text(json.dumps({"name": "bin/llama-server.exe"}), encoding="utf-8")
            self.assertFalse(local_translation._runtime_valid(directory))

    def test_ensure_llama_runtime_rejects_unsafe_archive_member_before_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = local_translation.cache_root(tmp) / "runtimes"
            root.mkdir(parents=True)
            archive = root / "runtime.zip"
            import zipfile
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../escape.txt", "bad")
                zipped.writestr("bin/llama-server.exe", "exe")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            fake_requests = types.SimpleNamespace(get=mock.Mock(side_effect=AssertionError("outbound request attempted")))

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.object(local_translation, "_RUNTIME_FILES", {"cpu": [(archive.name, archive.stat().st_size, digest)]}), \
                 mock.patch.dict(sys.modules, {"requests": fake_requests}):
                with self.assertRaisesRegex(RuntimeError, "Unsafe runtime archive member"):
                    local_translation.ensure_llama_runtime("cpu", offline=False, cache_dir=tmp)

            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_ensure_llama_runtime_resets_full_size_partial_before_resume_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = local_translation.cache_root(tmp) / "runtimes"
            root.mkdir(parents=True)
            archive_name = "runtime.zip"
            archive_path = root / archive_name

            import zipfile
            with zipfile.ZipFile(archive_path, "w") as zipped:
                zipped.writestr("bin/llama-server.exe", "exe")
            archive_bytes = archive_path.read_bytes()
            archive_path.unlink()
            stale_part = root / f"{archive_name}.part"
            stale_part.write_bytes(b"x" * len(archive_bytes))
            digest = hashlib.sha256(archive_bytes).hexdigest()
            requests_get = mock.Mock(return_value=FakeDownloadResponse(archive_bytes))
            fake_requests = types.SimpleNamespace(get=requests_get)

            with mock.patch.object(local_translation.sys, "platform", "win32"), \
                 mock.patch.object(local_translation, "_RUNTIME_FILES", {"cpu": [(archive_name, len(archive_bytes), digest)]}), \
                 mock.patch.dict(sys.modules, {"requests": fake_requests}):
                executable = local_translation.ensure_llama_runtime("cpu", offline=False, cache_dir=tmp)

            self.assertEqual(executable.name, "llama-server.exe")
            self.assertFalse(stale_part.exists())
            self.assertEqual(requests_get.call_args.kwargs["headers"], {})

    def test_get_translation_engine_validates_device_and_cuda_before_model_prepare(self):
        with self.assertRaisesRegex(ValueError, "auto, cpu or cuda"):
            local_translation.get_translation_engine("hy-mt2-1.8b-q8", "it", "fr", device="tpu")

        with mock.patch.object(local_translation, "_gpu_available", return_value=False), \
             mock.patch.object(local_translation, "ensure_model", side_effect=AssertionError("model prepared")):
            with self.assertRaisesRegex(RuntimeError, "CUDA was explicitly requested"):
                local_translation.get_translation_engine("hy-mt2-1.8b-q8", "it", "fr", device="cuda")

    def test_get_translation_engine_passes_offline_to_model_prepare_and_reuses_open_engine(self):
        progress_events = []
        first_cancel = lambda: False
        second_cancel = lambda: True

        class FakeEngine:
            contextual = True
            max_input_tokens = 2048

            def __init__(self, spec, path, device, offline, progress, cancel, cache_dir):
                self.spec = spec
                self.path = path
                self.device = device
                self.offline = offline
                self.progress = progress
                self.cancel = cancel
                self.cache_dir = cache_dir
                self.closed = False

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(local_translation, "_gpu_available", return_value=False), \
                 mock.patch.object(local_translation, "ensure_model", return_value=Path(tmp) / "model.gguf") as ensure_model, \
                 mock.patch.object(local_translation, "LlamaTranslationEngine", FakeEngine):
                first = local_translation.get_translation_engine(
                    "hy-mt2-1.8b-q8",
                    "it",
                    "fr",
                    offline=True,
                    progress=progress_events.append,
                    cancel=first_cancel,
                    cache_dir=tmp,
                )
                second = local_translation.get_translation_engine(
                    "hy-mt2-1.8b-q8",
                    "it",
                    "fr",
                    offline=True,
                    progress=None,
                    cancel=second_cancel,
                    cache_dir=tmp,
                )

        self.assertIs(first, second)
        self.assertIs(second.progress, None)
        self.assertIs(second.cancel, second_cancel)
        self.assertTrue(any(event["state"] == "loading" for event in progress_events))
        ensure_model.assert_called_once()
        self.assertIs(ensure_model.call_args.args[1], True)

    def test_llama_translation_schema_requires_id_round_trip(self):
        engine = local_translation.LlamaTranslationEngine.__new__(local_translation.LlamaTranslationEngine)
        engine.cancel = None
        engine.max_input_tokens = 2048
        engine.session = FakeLlamaSession(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"translations": [{"id": "1", "text": "Bonjour"}]})},
                    }
                ]
            }
        )
        engine.url = "http://127.0.0.1:1234"

        with self.assertRaisesRegex(ValueError, "preserve unit IDs"):
            engine.translate(["ciao"], "it", "fr")

        completion_request = engine.session.posts[-1]["json"]
        schema = completion_request["response_format"]["json_schema"]["schema"]
        item_schema = schema["properties"]["translations"]["items"]
        self.assertEqual(item_schema["required"], ["id", "text"])
        self.assertTrue(completion_request["response_format"]["json_schema"]["strict"])

    def test_llama_translation_normalizes_malformed_json_missing_fields_for_pipeline_retry(self):
        engine = local_translation.LlamaTranslationEngine.__new__(local_translation.LlamaTranslationEngine)
        engine.cancel = None
        engine.max_input_tokens = 2048
        engine.session = FakeLlamaSession(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"translations": [{"id": "0"}]})},
                    }
                ]
            }
        )
        engine.url = "http://127.0.0.1:1234"

        with self.assertRaisesRegex(ValueError, "Malformed translation response: invalid JSON structure or missing unit fields"):
            engine.translate(["ciao"], "it", "fr")

    def test_llama_translation_includes_context_and_glossary_in_prompt_data(self):
        engine = local_translation.LlamaTranslationEngine.__new__(local_translation.LlamaTranslationEngine)
        engine.cancel = None
        engine.max_input_tokens = 2048
        engine.session = FakeLlamaSession(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"translations": [{"id": "0", "text": "Bonjour"}]})},
                    }
                ]
            }
        )
        engine.url = "http://127.0.0.1:1234"

        result = engine.translate(["ciao"], "it", "fr", context="formal dialogue", glossary={"ciao": "bonjour"})

        prompt = engine.session.posts[-1]["json"]["messages"][0]["content"]
        self.assertEqual(result, ["Bonjour"])
        self.assertIn("Background dialogue", prompt)
        self.assertIn("formal dialogue", prompt)
        self.assertIn("Terminology", prompt)
        self.assertIn("bonjour", prompt)

    def test_llama_translation_cancel_closes_local_process_during_generation(self):
        engine = local_translation.LlamaTranslationEngine.__new__(local_translation.LlamaTranslationEngine)
        process = FakeProcess()
        cancel_checks = iter([False, True])
        engine.cancel = lambda: next(cancel_checks, True)
        engine.max_input_tokens = 2048
        engine.session = BlockingLlamaSession()
        engine.url = "http://127.0.0.1:1234"
        engine.process = process
        engine.log = tempfile.TemporaryFile(mode="w+b")

        try:
            with self.assertRaises(model_manager.CancelledError):
                engine.translate(["ciao"], "it", "fr")
            self.assertTrue(process.terminated)
        finally:
            engine.log.close()

    def test_llama_auto_cuda_memory_pressure_reduces_units_then_retries_same_model_on_cpu(self):
        progress_events = []
        engine = local_translation.LlamaTranslationEngine.__new__(local_translation.LlamaTranslationEngine)
        engine.spec = types.SimpleNamespace(id="hy-mt2-1.8b-q8")
        engine.model_path = "same-model.gguf"
        engine.device = "cuda"
        engine.offline = True
        engine.cache_dir = "cache"
        engine.progress = progress_events.append
        engine.cancel = None
        engine.allow_cpu = True
        attempts = []
        closed = []
        reinitialized = []

        def fake_translate(texts, source_language, target_language, context="", glossary=None):
            attempts.append((engine.device, tuple(texts)))
            if engine.device == "cuda":
                raise RuntimeError("CUDA out of memory")
            return [f"cpu:{text}" for text in texts]

        def fake_init(self, spec, model_path, device, offline=False, progress=None, cancel=None, cache_dir=None):
            reinitialized.append((spec, model_path, device, offline, progress, cancel, cache_dir))
            self.spec = spec
            self.model_path = model_path
            self.device = device
            self.offline = offline
            self.progress = progress
            self.cancel = cancel
            self.cache_dir = cache_dir
            self.allow_cpu = False

        engine._translate = fake_translate

        with mock.patch.object(local_translation.LlamaTranslationEngine, "close", side_effect=lambda: closed.append(True)), \
             mock.patch.object(local_translation.LlamaTranslationEngine, "__init__", fake_init):
            result = engine.translate(["one", "two"], "it", "fr")

        self.assertEqual(result, ["cpu:one", "cpu:two"])
        self.assertEqual(
            attempts,
            [
                ("cuda", ("one", "two")),
                ("cuda", ("one",)),
                ("cpu", ("one",)),
                ("cpu", ("two",)),
            ],
        )
        self.assertEqual(closed, [True])
        self.assertEqual(reinitialized[0][1:4], ("same-model.gguf", "cpu", True))
        messages = [event["message"] for event in progress_events]
        self.assertIn("Reducing dialogue batch after GPU memory pressure", messages)
        self.assertIn("GPU memory exhausted; retrying the same model on CPU", messages)

    def test_ct2_translation_emits_notice_when_context_or_glossary_is_supplied(self):
        progress_events = []
        engine = local_translation.CT2TranslationEngine.__new__(local_translation.CT2TranslationEngine)
        engine.spec = types.SimpleNamespace(family="opus")
        engine.tokenizer = FakeTokenizer()
        engine.model = FakeCT2Model(["salut"])
        engine.progress = progress_events.append
        engine.cancel = None
        engine.max_input_tokens = 480
        engine.device = "cpu"
        engine.allow_cpu = False

        with mock.patch.dict(sys.modules, {"ctranslate2": types.SimpleNamespace()}):
            output = engine.translate(["hello"], "en", "fr", context="ignored", glossary={"hello": "salut"})

        self.assertEqual(output, ["salut"])
        self.assertEqual(progress_events[0]["state"], "notice")
        self.assertIn("contextual instructions are not supported", progress_events[0]["message"])

    def test_ct2_translation_preserves_nllb_language_prefix_contract(self):
        engine = local_translation.CT2TranslationEngine.__new__(local_translation.CT2TranslationEngine)
        engine.spec = types.SimpleNamespace(family="nllb")
        engine.tokenizer = FakeTokenizer()
        engine.model = FakeCT2Model(["salut"])
        engine.progress = None
        engine.cancel = None
        engine.max_input_tokens = 480
        engine.device = "cpu"
        engine.allow_cpu = False

        with mock.patch.dict(sys.modules, {"ctranslate2": types.SimpleNamespace()}):
            output = engine.translate(["hello"], "en", "fr")

        self.assertEqual(output, ["salut"])
        self.assertEqual(engine.tokenizer.src_lang, "eng_Latn")
        self.assertEqual(engine.model.calls[0]["kwargs"]["target_prefix"], [["fra_Latn"]])

    def test_ct2_translation_reduces_batch_after_memory_pressure(self):
        progress_events = []
        engine = local_translation.CT2TranslationEngine.__new__(local_translation.CT2TranslationEngine)
        engine.spec = types.SimpleNamespace(family="opus")
        engine.tokenizer = FakeTokenizer()
        engine.model = MemoryPressureCT2Model()
        engine.progress = progress_events.append
        engine.cancel = None
        engine.max_input_tokens = 480
        engine.device = "cpu"
        engine.allow_cpu = False

        with mock.patch.dict(sys.modules, {"ctranslate2": types.SimpleNamespace()}):
            output = engine.translate(["a", "b"], "en", "fr")

        self.assertEqual(output, ["ok:a", "ok:b"])
        self.assertEqual(len(engine.model.calls), 3)
        self.assertIn("Reducing translation batch", progress_events[0]["message"])

    def test_ct2_translation_auto_cuda_falls_back_to_cpu_on_single_batch_oom(self):
        progress_events = []
        cuda_model = AlwaysOOMCT2Model()
        cpu_model = FakeCT2Model(["salut"])
        created = []

        def fake_translator(path, device, compute_type):
            created.append((path, device, compute_type))
            self.assertEqual(device, "cpu")
            return cpu_model

        engine = local_translation.CT2TranslationEngine.__new__(local_translation.CT2TranslationEngine)
        engine.spec = types.SimpleNamespace(family="opus")
        engine.tokenizer = FakeTokenizer()
        engine.model = cuda_model
        engine.progress = progress_events.append
        engine.cancel = None
        engine.max_input_tokens = 480
        engine.device = "cuda"
        engine.allow_cpu = True
        engine.path = "prepared-model"

        fake_ctranslate2 = types.SimpleNamespace(Translator=fake_translator)
        with mock.patch.dict(sys.modules, {"ctranslate2": fake_ctranslate2}):
            output = engine.translate(["hello"], "en", "fr")

        self.assertEqual(output, ["salut"])
        self.assertTrue(cuda_model.unloaded)
        self.assertEqual(engine.device, "cpu")
        self.assertEqual(created, [("prepared-model", "cpu", "int8")])
        self.assertIn("retrying the same model on CPU", progress_events[0]["message"])


if __name__ == "__main__":
    unittest.main()

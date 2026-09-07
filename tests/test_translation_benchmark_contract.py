import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import translation_benchmark


class FakeScore:
    score = 88.5


class FakeCHRF:
    def __init__(self, word_order):
        self.word_order = word_order

    def sentence_score(self, output, references):
        return FakeScore()


class FakeMemorySampler:
    def __enter__(self):
        self.peak_rss_bytes = 1234
        self.peak_gpu_mib = None
        return self

    def __exit__(self, *_exc_info):
        return False


class FakeEngine:
    def __init__(self, label, contextual):
        self.label = label
        self.contextual = contextual
        self.max_input_tokens = 128
        self.calls = []

    def translate(self, texts, source_language, target_language, context=""):
        self.calls.append(
            {
                "texts": list(texts),
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
            }
        )
        return [f"{target_language}:{texts[0]}"]


def fake_sacrebleu_modules():
    metrics = types.SimpleNamespace(CHRF=FakeCHRF)
    return {"sacrebleu": types.SimpleNamespace(metrics=metrics), "sacrebleu.metrics": metrics}


class TranslationBenchmarkContractTests(unittest.TestCase):
    def write_examples(self, directory, rows):
        path = Path(directory) / "examples.jsonl"
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        return path

    def test_read_examples_requires_nonempty_valid_jsonl_and_normalizes_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            examples = self.write_examples(
                tmp,
                [
                    {
                        "source": "Italian",
                        "target": "FR",
                        "text": "ciao",
                        "reference": "bonjour",
                    }
                ],
            )
            bad = Path(tmp) / "bad.jsonl"
            bad.write_text('{"source":"en","target":"fr","text":"","reference":"bonjour"}\n', encoding="utf-8")
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("\n", encoding="utf-8")

            rows = translation_benchmark.read_examples(examples)

            self.assertEqual(rows[0]["source"], "it")
            self.assertEqual(rows[0]["target"], "fr")
            with self.assertRaisesRegex(ValueError, "Line 1 requires"):
                translation_benchmark.read_examples(bad)
            with self.assertRaisesRegex(ValueError, "No reference examples"):
                translation_benchmark.read_examples(empty)

    def test_main_runs_direct_and_via_english_legs_without_live_dependencies(self):
        calls = []
        close_calls = []
        contextual_engine = FakeEngine("ctx", contextual=True)
        sentence_engine = FakeEngine("sentence", contextual=False)

        def fake_resolve_model(model_id, source, target):
            return types.SimpleNamespace(id=f"{model_id}-{source}-{target}", revision=f"rev-{source}-{target}")

        def fake_get_translation_engine(model_id, source, target, device, offline):
            calls.append((model_id, source, target, device, offline))
            return contextual_engine if source == "it" else sentence_engine

        with tempfile.TemporaryDirectory() as tmp:
            examples = self.write_examples(
                tmp,
                [
                    {
                        "source": "it",
                        "target": "fr",
                        "text": "ciao",
                        "reference": "bonjour",
                        "context": "formal scene",
                    }
                ],
            )
            output = Path(tmp) / "report.json"
            with mock.patch.dict("sys.modules", fake_sacrebleu_modules()), \
                 mock.patch.object(translation_benchmark, "MemorySampler", FakeMemorySampler), \
                 mock.patch.object(translation_benchmark, "resolve_model", side_effect=fake_resolve_model), \
                 mock.patch.object(translation_benchmark, "get_translation_engine", side_effect=fake_get_translation_engine), \
                 mock.patch.object(translation_benchmark, "close_translation_engines", side_effect=lambda: close_calls.append(True)):
                exit_code = translation_benchmark.main(
                    [
                        str(examples),
                        "--models",
                        "opus-mt",
                        "--routes",
                        "direct",
                        "via-en",
                        "--device",
                        "cpu",
                        "--offline",
                        "--output-json",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual([row["route"] for row in report["results"]], ["direct", "via-en"])
        self.assertEqual(calls, [
            ("opus-mt", "it", "fr", "cpu", True),
            ("opus-mt", "it", "en", "cpu", True),
            ("opus-mt", "en", "fr", "cpu", True),
        ])
        self.assertEqual(contextual_engine.calls[0]["context"], "formal scene")
        self.assertEqual(contextual_engine.calls[1]["context"], "formal scene")
        self.assertEqual(sentence_engine.calls[0]["context"], "")
        self.assertEqual(report["results"][1]["intermediate"], "en:ciao")
        self.assertEqual(report["results"][1]["model_revisions"], ["rev-it-en", "rev-en-fr"])
        self.assertEqual(len(close_calls), 1)

    def test_main_skips_via_english_when_source_or_target_is_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            examples = self.write_examples(
                tmp,
                [{"source": "en", "target": "fr", "text": "hello", "reference": "bonjour"}],
            )
            output = Path(tmp) / "report.json"
            with mock.patch.dict("sys.modules", fake_sacrebleu_modules()), \
                 mock.patch.object(translation_benchmark, "MemorySampler", FakeMemorySampler), \
                 mock.patch.object(translation_benchmark, "get_translation_engine", side_effect=AssertionError("engine should not load")):
                exit_code = translation_benchmark.main(
                    [
                        str(examples),
                        "--models",
                        "opus-mt",
                        "--routes",
                        "via-en",
                        "--output-json",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["results"], [])

    def test_main_writes_failure_rows_incrementally_and_continues(self):
        calls = []

        def fake_get_translation_engine(model_id, source, target, device, offline):
            calls.append(model_id)
            if model_id == "bad":
                raise RuntimeError("CUDA out of memory")
            return FakeEngine("ok", contextual=False)

        with tempfile.TemporaryDirectory() as tmp:
            examples = self.write_examples(
                tmp,
                [{"source": "en", "target": "fr", "text": "hello", "reference": "bonjour"}],
            )
            output = Path(tmp) / "report.json"
            with mock.patch.dict("sys.modules", fake_sacrebleu_modules()), \
                 mock.patch.object(translation_benchmark, "MemorySampler", FakeMemorySampler), \
                 mock.patch.object(translation_benchmark, "resolve_model", return_value=types.SimpleNamespace(revision="rev")), \
                 mock.patch.object(translation_benchmark, "get_translation_engine", side_effect=fake_get_translation_engine):
                exit_code = translation_benchmark.main(
                    [
                        str(examples),
                        "--models",
                        "bad",
                        "good",
                        "--routes",
                        "direct",
                        "--output-json",
                        str(output),
                    ]
                )

            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(calls, ["bad", "good"])
        self.assertEqual([row["status"] for row in report["results"]], ["failed", "completed"])
        self.assertIn("CUDA out of memory", report["results"][0]["error"])
        self.assertIn("peak_rss_bytes", report["results"][0])


if __name__ == "__main__":
    unittest.main()

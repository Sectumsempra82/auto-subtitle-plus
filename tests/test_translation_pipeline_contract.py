import io
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import translation_pipeline, utils
from auto_subtitle_plus.translation_types import (
    RawTranslationCue,
    SourceCue,
    SourceWord,
    SubtitleCue,
    TranslationRequest,
    TranslationSettings,
)
from auto_subtitle_plus import model_manager


class FakeEngine:
    contextual = True
    max_input_tokens = 128

    def __init__(self):
        self.calls = []
        self.closed = False

    def translate(self, texts, source_language, target_language, context="", glossary=None):
        self.calls.append(
            {
                "texts": list(texts),
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
                "glossary": dict(glossary or {}),
            }
        )
        output = []
        for text in texts:
            translated = text
            for source_term, target_term in dict(glossary or {}).items():
                translated = translated.replace(source_term, target_term)
            output.append(f"{target_language}:{translated}")
        return output

    def close(self):
        self.closed = True


class NonContextualFakeEngine(FakeEngine):
    contextual = False

    def translate(self, *args, **kwargs):
        raise AssertionError("non-contextual engine should be rejected before translation")


class SentenceModelFakeEngine(FakeEngine):
    contextual = False
    max_input_tokens = 999

    def translate(self, texts, source_language, target_language, context="", glossary=None):
        self.calls.append(
            {
                "texts": list(texts),
                "source_language": source_language,
                "target_language": target_language,
                "context": context,
                "glossary": dict(glossary or {}),
            }
        )
        self.assert_sentence_unit = list(texts)
        return ["Bonjour le monde."]


class SpyEvent:
    def __init__(self):
        self._event = threading.Event()
        self._condition = threading.Condition()
        self.clear_count = 0

    def set(self):
        with self._condition:
            self._event.set()
            self._condition.notify_all()

    def clear(self):
        with self._condition:
            self.clear_count += 1
            self._event.clear()
            self._condition.notify_all()

    def is_set(self):
        return self._event.is_set()

    def wait_for_clear_count(self, count, timeout=2.0):
        with self._condition:
            return self._condition.wait_for(lambda: self.clear_count >= count, timeout)


def source_cue(
    cue_id,
    start,
    end,
    text="ciao",
    language="it",
    speaker=None,
):
        return SourceCue(
        id=cue_id,
        index=int(cue_id.removeprefix("cue") or "1"),
        start=start,
        end=end,
        text=text,
        language=language,
        speaker=speaker,
    )


class TranslationPipelineContractTests(unittest.TestCase):
    def test_clear_translation_cache_removes_stage_caches_but_retains_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stage in ("source", "translation", "layout"):
                (root / stage).mkdir()
                (root / stage / "entry.json").write_text("{}", encoding="utf-8")
            (root / "models" / "source" / "repo").mkdir(parents=True)
            (root / "models" / "source" / "repo" / "model.bin").write_bytes(b"model")

            translation_pipeline.clear_translation_cache(str(root))

            self.assertFalse((root / "source").exists())
            self.assertFalse((root / "translation").exists())
            self.assertFalse((root / "layout").exists())
            self.assertTrue((root / "models" / "source" / "repo" / "model.bin").is_file())

    def test_clear_translation_cache_refuses_resolved_stage_path_outside_cache_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cache"
            root.mkdir()
            outside = Path(tmp) / "outside-source"
            outside.mkdir()
            original_resolve = Path.resolve

            def fake_resolve(path, *args, **kwargs):
                if path == root / "source":
                    return outside
                return original_resolve(path, *args, **kwargs)

            with mock.patch.object(translation_pipeline.Path, "resolve", fake_resolve), \
                 mock.patch.object(translation_pipeline.shutil, "rmtree", side_effect=AssertionError("outside path removed")):
                with self.assertRaisesRegex(translation_pipeline.TranslationConfigError, "Refusing to clear cache outside"):
                    translation_pipeline.clear_translation_cache(str(root))

            self.assertTrue(outside.is_dir())

    def test_translation_cache_key_includes_model_revision_context_and_layout_settings(self):
        base = TranslationSettings(source_language="it", target_language="fr")
        different_layout = TranslationSettings(
            source_language="it",
            target_language="fr",
            max_chars_per_line=32,
        )

        base_key = translation_pipeline.translation_cache_key(
            "source-content-key",
            "it",
            "fr",
            base.route,
            base.engine,
            "hy-mt2-1.8b-q8",
            "revision-1",
            base,
            "final",
            "scene context",
            {"ciao": "bonjour"},
            "cue-digest",
        )
        revision_key = translation_pipeline.translation_cache_key(
            "source-content-key",
            "it",
            "fr",
            base.route,
            base.engine,
            "hy-mt2-1.8b-q8",
            "revision-2",
            base,
            "final",
            "scene context",
            {"ciao": "bonjour"},
            "cue-digest",
        )
        layout_key = translation_pipeline.translation_cache_key(
            "source-content-key",
            "it",
            "fr",
            different_layout.route,
            different_layout.engine,
            "hy-mt2-1.8b-q8",
            "revision-1",
            different_layout,
            "final",
            "scene context",
            {"ciao": "bonjour"},
            "cue-digest",
        )
        context_key = translation_pipeline.translation_cache_key(
            "source-content-key",
            "it",
            "fr",
            base.route,
            base.engine,
            "hy-mt2-1.8b-q8",
            "revision-1",
            base,
            "final",
            "different context",
            {"ciao": "bonjour"},
            "cue-digest",
        )
        cue_key = translation_pipeline.translation_cache_key(
            "source-content-key",
            "it",
            "fr",
            base.route,
            base.engine,
            "hy-mt2-1.8b-q8",
            "revision-1",
            base,
            "final",
            "scene context",
            {"ciao": "bonjour"},
            "different-cue-digest",
        )

        self.assertNotEqual(base_key, revision_key)
        self.assertNotEqual(base_key, layout_key)
        self.assertNotEqual(base_key, context_key)
        self.assertNotEqual(base_key, cue_key)

    def test_layout_cache_key_includes_layout_revision(self):
        settings = TranslationSettings(source_language="en", target_language="it")
        raw = (
            RawTranslationCue(
                id="raw-1",
                source_ids=("cue1",),
                start=0.0,
                end=1.0,
                text="ciao",
                language="it",
                source_text="hello",
            ),
        )

        base_key = translation_pipeline.layout_cache_key("source-key", "final-layout", "it", settings, raw, False)
        with mock.patch.object(translation_pipeline, "LAYOUT_REVISION", "different-layout-revision"):
            changed_key = translation_pipeline.layout_cache_key("source-key", "final-layout", "it", settings, raw, False)

        self.assertNotEqual(base_key, changed_key)

    def test_layout_stage_ignores_stale_three_line_layout_cache_from_previous_revision(self):
        settings = TranslationSettings(source_language="en", target_language="it")
        translated = "a" * 30 + " " + "b" * 12 + " " + "c" * 30 + " " + "d" * 9
        raw = (
            RawTranslationCue(
                id="raw-1",
                source_ids=("cue1",),
                start=0.0,
                end=7.0,
                text=translated,
                language="it",
                source_text="source",
                metadata={"stage": "final"},
            ),
        )
        stale_cue = SubtitleCue(
            id="stale-layout",
            source_ids=("cue1",),
            start=0.0,
            end=7.0,
            text="a" * 30 + "\n" + "b" * 12 + " " + "c" * 30 + "\n" + "d" * 9,
            language="it",
            source_text=None,
            metadata={"layout_revision": "stale-layout-revision"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            request = TranslationRequest(
                source_key="source-key",
                cues=(source_cue("cue1", 0.0, 7.0, text="source", language="en"),),
                settings=settings,
                cache_dir=tmp,
            )
            with mock.patch.object(translation_pipeline, "LAYOUT_REVISION", "stale-layout-revision"):
                stale_key = translation_pipeline.layout_cache_key(
                    request.source_key,
                    "final-layout",
                    "it",
                    settings,
                    raw,
                    False,
                )
            current_key = translation_pipeline.layout_cache_key(
                request.source_key,
                "final-layout",
                "it",
                settings,
                raw,
                False,
            )
            translation_pipeline.write_json_cache(
                tmp,
                "layout",
                stale_key,
                {
                    "revision": translation_pipeline.PIPELINE_REVISION,
                    "layout_revision": "stale-layout-revision",
                    "cues": [translation_pipeline.subtitle_cue_to_dict(stale_cue)],
                },
            )

            cues = translation_pipeline.TranslationPipeline(tmp)._layout_stage(request, raw, "it", "final", False)

            self.assertTrue(os.path.exists(translation_pipeline.cache_path(tmp, "layout", current_key)))

        self.assertNotEqual(stale_key, current_key)
        self.assertNotEqual([cue.text for cue in cues], [stale_cue.text])
        self.assertEqual(" ".join(cue.text.replace("\n", " ") for cue in cues), translated)
        for cue in cues:
            lines = cue.text.splitlines()
            self.assertLessEqual(len(lines), settings.max_lines)
            for line in lines:
                self.assertLessEqual(len(line), settings.max_chars_per_line)

    def test_source_cache_key_uses_input_content_and_transcription_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "clip.wav"
            media.write_bytes(b"one")
            first = translation_pipeline.source_cache_key(str(media), {"model": "small"})
            media.write_bytes(b"two")
            changed_content = translation_pipeline.source_cache_key(str(media), {"model": "small"})
            changed_settings = translation_pipeline.source_cache_key(str(media), {"model": "large"})

        self.assertNotEqual(first, changed_content)
        self.assertNotEqual(changed_content, changed_settings)

    def test_source_cache_round_trips_immutable_source_cues(self):
        cues = (
            source_cue("cue1", 0.0, 1.0, text="ciao", speaker="A"),
            source_cue("cue2", 1.1, 2.0, text="mondo", speaker="A"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            translation_pipeline.cache_source_transcript(tmp, "source-key", cues, "it")
            cached = translation_pipeline.read_cached_source_transcript(tmp, "source-key")

        self.assertIsNotNone(cached)
        language, cached_cues = cached
        self.assertEqual(language, "it")
        self.assertEqual(cached_cues, cues)

    def test_load_local_translation_engine_uses_supervisor_interface_exactly(self):
        calls = {}
        fake_engine = FakeEngine()

        def fake_get_translation_engine(*args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return fake_engine

        fake_module = types.SimpleNamespace(get_translation_engine=fake_get_translation_engine)
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0),),
            settings=TranslationSettings(
                source_language="it",
                target_language="fr",
                model_id="hy-mt2-1.8b-q8",
                offline=True,
            ),
            cache_dir="cache-here",
            progress=lambda *_args: None,
            cancel=lambda: False,
        )

        with mock.patch.dict(sys.modules, {"auto_subtitle_plus.local_translation": fake_module}):
            engine = translation_pipeline.load_local_translation_engine(
                request.settings,
                "it",
                "fr",
                request,
                "hy-mt2-1.8b-q8",
            )

        self.assertIs(engine, fake_engine)
        self.assertEqual(calls["args"], ("hy-mt2-1.8b-q8", "it", "fr"))
        self.assertEqual(
            calls["kwargs"],
            {
                "device": "auto",
                "offline": True,
                "progress": request.progress,
                "cancel": request.cancel,
                "cache_dir": "cache-here",
            },
        )

    def test_pipeline_direct_translation_preserves_source_ids_times_and_passes_context_glossary(self):
        engine = FakeEngine()
        pipeline = translation_pipeline.TranslationPipeline()
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.25, text="ciao", speaker="A"),),
            settings=TranslationSettings(source_language="it", target_language="fr", bilingual=True),
            context="speaker A is formal",
            glossary={"ciao": "bonjour"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            request = TranslationRequest(
                source_key=request.source_key,
                cues=request.cues,
                settings=request.settings,
                context=request.context,
                glossary=request.glossary,
                cache_dir=tmp,
            )
            with mock.patch.object(translation_pipeline, "load_local_translation_engine", return_value=engine), \
                 mock.patch.object(translation_pipeline, "write_json_cache"):
                result = pipeline.translate(request)

        self.assertFalse(result.cache_hit)
        self.assertEqual(result.source_cues, request.cues)
        self.assertEqual(engine.calls[0]["context"], "speaker A is formal")
        self.assertEqual(engine.calls[0]["glossary"], {"ciao": "bonjour"})
        self.assertEqual(result.final_cues[0].source_ids, ("cue1",))
        self.assertEqual(result.final_cues[0].start, 0.0)
        self.assertEqual(result.final_cues[0].end, 1.25)
        self.assertEqual(result.final_cues[0].text, "fr:bonjour")
        self.assertEqual(result.final_cues[0].source_text, "ciao")

    def test_pipeline_uses_cached_final_without_loading_engine(self):
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0, text="ciao"),),
            settings=TranslationSettings(source_language="it", target_language="fr"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            request = TranslationRequest(
                source_key=request.source_key,
                cues=request.cues,
                settings=request.settings,
                cache_dir=tmp,
            )
            key = translation_pipeline.raw_translation_cache_key(
                request.source_key,
                "it",
                "fr",
                request.settings.route,
                request.settings.engine,
                "hy-mt2-1.8b-q8",
                translation_pipeline.TranslationPipeline()._effective_leg_model_revision(request.settings, "it", "fr"),
                request.settings,
                "final-raw",
                request.context,
                dict(request.glossary or {}),
                translation_pipeline.cue_digest(request.cues),
                unit_mode=translation_pipeline.raw_unit_mode(request.settings),
            )
            translation_pipeline.write_json_cache(
                tmp,
                "translation",
                key,
                {
                    "revision": translation_pipeline.PIPELINE_REVISION,
                    "raw_cues": [
                        {
                            "id": "cue1-raw-1",
                            "source_ids": ["cue1"],
                            "start": 0.0,
                            "end": 1.0,
                            "text": "fr:ciao",
                            "language": "fr",
                            "source_text": "ciao",
                            "speaker": None,
                            "metadata": {
                                "stage": "final",
                                "pipeline_revision": translation_pipeline.PIPELINE_REVISION,
                                "exact_timing": True,
                                "timing_estimated": False,
                            },
                        }
                    ],
                },
            )
            with mock.patch.object(
                translation_pipeline,
                "load_local_translation_engine",
                side_effect=AssertionError("engine loaded despite cache"),
            ):
                result = translation_pipeline.TranslationPipeline().translate(request)

        self.assertTrue(result.cache_hit)
        self.assertEqual(result.final_cues[0].text, "fr:ciao")

    def test_pipeline_rejects_google_offline_and_via_english_with_english_endpoint(self):
        cue = source_cue("cue1", 0.0, 1.0, language="en")
        pipeline = translation_pipeline.TranslationPipeline()

        for settings in (
            TranslationSettings(source_language="en", target_language="fr", route="via-en"),
            TranslationSettings(source_language="fr", target_language="en", route="via-en"),
            TranslationSettings(source_language="en", target_language="fr", engine="google", offline=True),
        ):
            with self.subTest(settings=settings):
                request = TranslationRequest("source-key", (cue,), settings)
                with self.assertRaises(translation_pipeline.TranslationConfigError):
                    pipeline.translate(request)

    def test_explicit_google_allows_broad_languages_without_local_catalog_resolution(self):
        class FakeGoogleEngine:
            contextual = False
            max_input_tokens = 512

            def __init__(self):
                self.calls = []

            def translate(self, texts, source_language, target_language, context="", glossary=None):
                self.calls.append((list(texts), source_language, target_language, context, glossary))
                return ["Merhaba"]

            def close(self):
                pass

        engine = FakeGoogleEngine()
        pipeline = translation_pipeline.TranslationPipeline()
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0, text="Bonjour", language="fr"),),
            settings=TranslationSettings(source_language="fr", target_language="tr", engine="google"),
        )

        with tempfile.TemporaryDirectory() as tmp:
            request = TranslationRequest(
                source_key=request.source_key,
                cues=request.cues,
                settings=request.settings,
                cache_dir=tmp,
            )
            with mock.patch.object(translation_pipeline, "GoogleTranslationEngine", return_value=engine), \
                 mock.patch("auto_subtitle_plus.model_manager.resolve_model", side_effect=AssertionError("local catalog used for google")), \
                 mock.patch.object(translation_pipeline, "write_json_cache"):
                result = pipeline.translate(request)

        self.assertEqual(engine.calls, [(["Bonjour"], "fr", "tr", "", {})])
        self.assertEqual(result.model_id, None)
        self.assertTrue(result.model_revision.startswith("google-deep-translator:"))
        self.assertEqual(result.final_cues[0].text, "Merhaba")
        self.assertEqual(result.final_cues[0].language, "tr")

    def test_pipeline_rejects_context_and_glossary_for_non_contextual_engine(self):
        pipeline = translation_pipeline.TranslationPipeline()
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0, text="hello", language="en"),),
            settings=TranslationSettings(source_language="en", target_language="fr"),
            context="scene context",
            glossary={"hello": "salut"},
        )

        with mock.patch.object(
            translation_pipeline,
            "load_local_translation_engine",
            return_value=NonContextualFakeEngine(),
        ), mock.patch.object(translation_pipeline, "write_json_cache"):
            with self.assertRaisesRegex(translation_pipeline.TranslationConfigError, "context|glossary"):
                pipeline.translate(request)

    def test_sentence_model_merges_restore_preserve_layout_and_bilingual_source_cues(self):
        engine = SentenceModelFakeEngine()
        pipeline = translation_pipeline.TranslationPipeline()
        cues = (
            source_cue("cue1", 0.0, 1.0, text="Hello", language="en", speaker="A"),
            source_cue("cue2", 1.05, 2.0, text="world.", language="en", speaker="A"),
        )
        request = TranslationRequest(
            source_key="source-key",
            cues=cues,
            settings=TranslationSettings(
                source_language="en",
                target_language="fr",
                model_id="opus-en-fr",
                bilingual=True,
                adaptive_layout=False,
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            request = TranslationRequest(
                source_key=request.source_key,
                cues=request.cues,
                settings=request.settings,
                cache_dir=tmp,
            )
            with mock.patch.object(translation_pipeline, "load_local_translation_engine", return_value=engine), \
                 mock.patch.object(translation_pipeline, "write_json_cache"):
                result = pipeline.translate(request)

        self.assertEqual(engine.calls[0]["texts"], ["Hello world."])
        self.assertEqual([cue.source_ids for cue in result.final_cues], [("cue1",), ("cue2",)])
        self.assertEqual([(cue.start, cue.end) for cue in result.final_cues], [(0.0, 1.0), (1.05, 2.0)])
        self.assertEqual([cue.source_text for cue in result.final_cues], ["Hello", "world."])
        self.assertEqual(" ".join(cue.text for cue in result.final_cues), "Bonjour le monde.")

    def test_translation_units_keep_gap_speaker_and_overlap_hard_boundaries(self):
        settings = TranslationSettings(source_language="it", target_language="fr")
        cues = (
            source_cue("cue1", 0.00, 1.00, speaker="A"),
            source_cue("cue2", 1.25, 2.00, speaker="A"),
            source_cue("cue3", 2.26, 3.00, speaker="A"),
            source_cue("cue4", 3.05, 4.00, speaker="B"),
            source_cue("cue5", 3.90, 5.00, speaker="B"),
        )

        units = translation_pipeline.translation_units(cues, settings, max_input_tokens=999)

        self.assertEqual(
            [[cue.id for cue in unit] for unit in units],
            [["cue1", "cue2"], ["cue3"], ["cue4"], ["cue5"]],
        )

    def test_adaptive_layout_stays_within_source_span_and_preserves_all_text(self):
        settings = TranslationSettings(source_language="it", target_language="fr")
        cue = source_cue(
            "cue1",
            10.0,
            20.0,
            text="source",
        )
        translated = (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
            "nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
        )

        cues = translation_pipeline.layout_translated_cue(cue, translated, "fr", settings)
        combined = " ".join(item.text.replace("\n", " ") for item in cues)

        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(cues[0].start, 10.0)
        self.assertEqual(cues[-1].end, 20.0)
        for item in cues:
            self.assertGreaterEqual(item.end - item.start, settings.min_duration)
            self.assertLessEqual(item.end - item.start, settings.max_duration)
            self.assertLessEqual(len(item.text), settings.max_chars_per_line * settings.max_lines)
            self.assertEqual(item.source_ids, ("cue1",))
        self.assertEqual(combined, translated)

    def test_adaptive_layout_wraps_rendered_text_to_42_character_lines(self):
        settings = TranslationSettings(source_language="it", target_language="fr")
        cue = source_cue("cue1", 0.0, 5.0, text="source")
        translated = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"

        cues = translation_pipeline.layout_translated_cue(cue, translated, "fr", settings)

        self.assertTrue(any("\n" in cue.text for cue in cues))
        self.assertEqual(" ".join(cue.text.replace("\n", " ") for cue in cues), translated)
        for cue in cues:
            lines = cue.text.splitlines()
            self.assertLessEqual(len(lines), settings.max_lines)
            for line in lines:
                self.assertLessEqual(len(line), settings.max_chars_per_line)

    def test_adaptive_layout_splits_84_char_short_word_caption_before_it_wraps_to_three_lines(self):
        settings = TranslationSettings(source_language="en", target_language="it")
        cue = source_cue("cue1", 0.0, 7.0, text="source", language="en")
        translated = "a" * 30 + " " + "b" * 12 + " " + "c" * 30 + " " + "d" * 9

        self.assertEqual(len(translated), settings.max_chars_per_line * settings.max_lines)
        self.assertGreater(len(translation_pipeline.pack_words(translated.split(), settings.max_chars_per_line)), settings.max_lines)

        cues = translation_pipeline.layout_translated_cue(cue, translated, "it", settings)

        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(" ".join(item.text.replace("\n", " ") for item in cues), translated)
        for item in cues:
            lines = item.text.splitlines()
            self.assertLessEqual(len(lines), settings.max_lines)
            self.assertFalse(any(len(word) > settings.max_chars_per_line for word in item.text.split()))
            for line in lines:
                self.assertLessEqual(len(line), settings.max_chars_per_line)
            self.assertNotIn("layout_impossible_chars", item.metadata.get("warnings", ()))

    def test_word_timing_anchors_drive_split_source_cue_timing(self):
        cue = SourceCue(
            id="cue1",
            index=1,
            start=0.0,
            end=10.0,
            text="one two three four",
            language="en",
            words=(
                SourceWord("cue1-w1", 0.0, 0.4, "one"),
                SourceWord("cue1-w2", 0.5, 1.0, "two"),
                SourceWord("cue1-w3", 8.0, 8.5, "three"),
                SourceWord("cue1-w4", 9.0, 10.0, "four"),
            ),
        )

        split = translation_pipeline.split_source_cue_for_translation(cue)

        self.assertIsNotNone(split)
        first, second = split
        self.assertEqual(first.text, "one two")
        self.assertEqual(second.text, "three four")
        self.assertAlmostEqual(first.start, 0.0)
        self.assertLessEqual(first.end, 1.05)
        self.assertGreaterEqual(second.start, 7.95)
        self.assertAlmostEqual(second.end, 10.0)

    def test_validation_allows_german_capital_nouns_without_name_preservation(self):
        translation_pipeline.validate_translated_text("Das Auto ist rot.", "The car is red.", "de", "en", {}, ())

    def test_validation_allows_legitimate_identical_short_outputs(self):
        translation_pipeline.validate_translated_text("OK", "OK", "en", "fr", {}, ())

        with self.assertRaisesRegex(translation_pipeline.TranslationValidationError, "untranslated source text"):
            translation_pipeline.validate_translated_text("This should translate", "This should translate", "en", "fr", {}, ())

    def test_validation_treats_medical_acronym_as_uncertain_not_strict_name(self):
        warnings = translation_pipeline.validate_translated_text(
            "Prepare the O.R. for the patient.",
            "Prepara la sala operatoria per il paziente.",
            "en",
            "it",
            {},
            (),
        )

        self.assertTrue(
            any("acronym" in warning or "entity" in warning for warning in warnings),
            warnings,
        )

    def test_validation_preserves_explicit_acronym_names_strictly(self):
        with self.assertRaisesRegex(translation_pipeline.TranslationValidationError, "preserve name 'O\\.R\\.'"):
            translation_pipeline.validate_translated_text(
                "Prepare the O.R. for the patient.",
                "Prepara la sala operatoria per il paziente.",
                "en",
                "it",
                {},
                ("O.R.",),
            )

        translation_pipeline.validate_translated_text(
            "Prepare the O.R. for the patient.",
            "Prepara la O.R. per il paziente.",
            "en",
            "it",
            {},
            ("O.R.",),
        )

    def test_validation_applies_explicit_acronym_glossary_without_name_preservation(self):
        translation_pipeline.validate_translated_text(
            "Prepare the O.R. for the patient.",
            "Prepara la sala operatoria per il paziente.",
            "en",
            "it",
            {"O.R.": "sala operatoria"},
            (),
        )

        with self.assertRaisesRegex(translation_pipeline.TranslationValidationError, "apply glossary term 'O\\.R\\.'"):
            translation_pipeline.validate_translated_text(
                "Prepare the O.R. for the patient.",
                "Prepara la camera per il paziente.",
                "en",
                "it",
                {"O.R.": "sala operatoria"},
                (),
            )

    def test_validation_compares_number_multisets_not_substrings(self):
        with self.assertRaisesRegex(translation_pipeline.TranslationValidationError, "preserve numbers"):
            translation_pipeline.validate_translated_text("Give 12 mg.", "Dare 120 mg.", "en", "it", {}, ())
        with self.assertRaisesRegex(translation_pipeline.TranslationValidationError, "preserve numbers"):
            translation_pipeline.validate_translated_text("Give 12 mg and 12 ml.", "Dare 12 mg e ml.", "en", "it", {}, ())

        translation_pipeline.validate_translated_text("Meet at 7:30.", "Rendez-vous a 7h30.", "en", "fr", {}, ())

    def test_reprojection_groups_short_translation_without_empty_chunks(self):
        raw = RawTranslationCue(
            id="raw-1",
            source_ids=("cue1", "cue2", "cue3"),
            start=0.0,
            end=3.0,
            text="Si",
            language="it",
            source_text="one two three",
            metadata={
                "source_cue_anchors": (
                    {"id": "cue1", "start": 0.0, "end": 1.0, "text": "one"},
                    {"id": "cue2", "start": 1.0, "end": 2.0, "text": "two"},
                    {"id": "cue3", "start": 2.0, "end": 3.0, "text": "three"},
                ),
            },
        )

        cues = translation_pipeline.reproject_raw_cues_to_source_boundaries((raw,))

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].source_ids, ("cue1", "cue2", "cue3"))
        self.assertEqual((cues[0].start, cues[0].end), (0.0, 3.0))
        self.assertEqual(cues[0].text, "Si")
        self.assertIn("text_allocation_estimated", cues[0].metadata["warnings"])

    def test_malformed_unsplittable_single_word_retries_once(self):
        class AlwaysMalformedEngine:
            contextual = False
            max_input_tokens = 128

            def __init__(self):
                self.calls = 0

            def translate(self, texts, source_language, target_language, context="", glossary=None):
                self.calls += 1
                raise ValueError("Malformed translation response: invalid JSON structure or missing unit fields")

        engine = AlwaysMalformedEngine()
        settings = TranslationSettings(source_language="en", target_language="it")
        request = TranslationRequest("source-key", (source_cue("cue1", 0.0, 1.0, text="Hi", language="en"),), settings)

        with self.assertRaisesRegex(ValueError, "Malformed translation response"):
            translation_pipeline.TranslationPipeline()._translate_raw_unit(
                engine,
                [request.cues[0]],
                "en",
                "it",
                request,
                settings,
                {},
                "final",
            )

        self.assertEqual(engine.calls, 2)

    def test_malformed_local_translation_response_is_retryable_marker(self):
        self.assertTrue(
            translation_pipeline.is_retryable_malformed_error(
                ValueError("Malformed translation response: invalid JSON structure or missing unit fields")
            )
        )

    def test_writers_require_pretranslated_cues_and_do_not_substitute_original_text(self):
        untranslated = {"segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}]}
        translated = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "Bonjour", "source_text": "Hello"},
            ]
        }

        with self.assertRaisesRegex(ValueError, "only serializes"):
            utils.write_subtitle(
                untranslated,
                io.StringIO(),
                subtitle_format="srt",
                translate_to="fr",
                translate_off=False,
            )

        output = io.StringIO()
        utils.write_subtitle(
            translated,
            output,
            subtitle_format="srt",
            translate_to="fr",
            translate_off=False,
            bilingual=False,
        )

        value = output.getvalue()
        self.assertIn("Bonjour", value)
        self.assertNotIn("Hello", value)
        self.assertNotIn("Translation error", value)

    def test_atomic_write_failure_leaves_existing_file_unchanged_and_no_part_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subtitle.srt"
            path.write_text("old", encoding="utf-8")

            with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    translation_pipeline.atomic_write_text(str(path), "new")

            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_gui_service_queued_translate_does_not_clear_active_cancel_event(self):
        active_started = threading.Event()
        release_active = threading.Event()
        observed_active_cancel = []

        class BlockingPipeline:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            def close(self):
                pass

            def translate(self, request):
                with self.lock:
                    self.calls += 1
                    call_index = self.calls
                if call_index == 1:
                    active_started.set()
                    release_active.wait(timeout=2)
                    observed_active_cancel.append(request.cancel())
                raise model_manager.CancelledError("Translation cancelled")

        service = translation_pipeline.TranslationGuiService()
        service._pipeline = BlockingPipeline()
        service._cancel_event = SpyEvent()
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0),),
            settings=TranslationSettings(source_language="it", target_language="fr"),
        )
        active_error = []
        queued_error = []

        def run_active():
            try:
                service.translate(request)
            except BaseException as error:
                active_error.append(error)

        def run_queued():
            try:
                service.translate(request)
            except BaseException as error:
                queued_error.append(error)

        active = threading.Thread(target=run_active)
        active.start()
        self.assertTrue(active_started.wait(timeout=2))
        self.assertEqual(service._cancel_event.clear_count, 1)

        service.cancel()
        queued = threading.Thread(target=run_queued)
        queued.start()
        service._cancel_event.wait_for_clear_count(2, timeout=0.2)
        release_active.set()
        active.join(timeout=2)

        self.assertFalse(active.is_alive())
        self.assertEqual(len(active_error), 1)
        self.assertEqual(observed_active_cancel, [True])

        queued.join(timeout=2)

    def test_gui_service_cancelled_error_sets_cancelled_state_not_failed(self):
        class CancelledPipeline:
            def close(self):
                pass

            def translate(self, _request):
                raise model_manager.CancelledError("Translation cancelled")

        service = translation_pipeline.TranslationGuiService()
        service._pipeline = CancelledPipeline()
        request = TranslationRequest(
            source_key="source-key",
            cues=(source_cue("cue1", 0.0, 1.0),),
            settings=TranslationSettings(source_language="it", target_language="fr"),
        )

        with self.assertRaises(model_manager.CancelledError):
            service.translate(request)

        self.assertEqual(service.state.state, "cancelled")
        self.assertFalse(service.state.cancellable)


if __name__ == "__main__":
    unittest.main()

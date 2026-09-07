import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from auto_subtitle_plus.translation_types import (
    SourceCue,
    SourceWord,
    SubtitleCue,
    TranslationSettings,
)


class TranslationFoundationContractTests(unittest.TestCase):
    def test_translation_settings_default_to_local_direct_adaptive_contract(self):
        settings = TranslationSettings(source_language="it", target_language="fr")

        self.assertEqual(settings.engine, "local")
        self.assertEqual(settings.route, "direct")
        self.assertFalse(settings.offline)
        self.assertTrue(settings.adaptive_layout)
        self.assertEqual(settings.max_chars_per_line, 42)
        self.assertEqual(settings.max_lines, 2)
        self.assertEqual(settings.max_cps, 17.0)
        self.assertEqual(settings.min_duration, 1.0)
        self.assertEqual(settings.max_duration, 7.0)
        self.assertEqual(settings.max_merge_gap, 0.25)

    def test_via_english_route_is_explicit_not_implicit(self):
        direct = TranslationSettings(source_language="fr", target_language="de")
        via_english = TranslationSettings(
            source_language="fr",
            target_language="de",
            route="via-en",
        )

        self.assertEqual(direct.route, "direct")
        self.assertEqual(via_english.route, "via-en")

    def test_source_and_output_cue_identity_language_and_timing_are_frozen(self):
        word = SourceWord(id="w1", start=0.1, end=0.4, text="Ciao")
        source = SourceCue(
            id="cue-1",
            index=1,
            start=0.0,
            end=1.0,
            text="Ciao mondo",
            language="it",
            words=(word,),
        )
        final = SubtitleCue(
            id="cue-1",
            source_ids=("cue-1",),
            start=0.0,
            end=1.0,
            text="Bonjour le monde",
            language="fr",
            source_text="Ciao mondo",
        )

        for cue, field, value in (
            (source, "id", "changed"),
            (source, "language", "en"),
            (source, "start", 9.0),
            (source, "end", 10.0),
            (final, "id", "changed"),
            (final, "source_ids", ("other",)),
            (final, "language", "en"),
            (final, "start", 9.0),
            (final, "end", 10.0),
        ):
            with self.subTest(cue=type(cue).__name__, field=field):
                with self.assertRaises(FrozenInstanceError):
                    setattr(cue, field, value)

    def test_catalog_metadata_is_pinned_offline_auditable_and_hashes_model_files(self):
        catalog_path = Path(__file__).resolve().parents[1] / "auto_subtitle_plus" / "translation_catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(catalog), 1)
        for model in catalog:
            with self.subTest(repo_id=model.get("repo_id")):
                self.assertIsInstance(model.get("repo_id"), str)
                self.assertIsInstance(model.get("revision"), str)
                self.assertGreaterEqual(len(model["revision"]), 12)
                self.assertIs(model.get("gated"), False)
                self.assertIsInstance(model.get("files"), list)
                self.assertGreaterEqual(len(model["files"]), 1)

                for file_info in model["files"]:
                    self.assertIsInstance(file_info.get("name"), str)
                    self.assertIsInstance(file_info.get("size"), int)
                    self.assertGreater(file_info["size"], 0)
                    self.assertIsInstance(file_info.get("git_blob"), str)

                    suffix = Path(file_info["name"]).suffix.lower()
                    if suffix in {".bin", ".safetensors", ".gguf", ".model"}:
                        self.assertIsInstance(file_info.get("sha256"), str)
                        self.assertEqual(len(file_info["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()

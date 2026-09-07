import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_windows


class WindowsBuildTests(unittest.TestCase):
    def test_source_archive_contains_rebuild_shortcut_but_no_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.zip"
            build_windows.source_archive(path)
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        self.assertIn("Build Portable.cmd", names)
        self.assertIn("packaging/Build-Portable.ps1", names)
        self.assertIn("packaging/windows.spec", names)
        self.assertIn("auto_subtitle_plus/translation_catalog.json", names)
        self.assertFalse(any(name.startswith(("docs/", ".git/", "dist/")) for name in names))
        self.assertNotIn("AGENTS.md", names)

    def test_finalizer_rejects_model_weights_and_user_data(self):
        for name in ("data/state.json", "_internal/model.gguf", "_internal/model.bin", "_internal/model.pt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                assets = output / "assets"
                assets.mkdir()
                (assets / "asset-manifest.json").write_text("{}")
                folder = output / "AutoSubtitlePlus-CLI"
                target = folder / name
                target.parent.mkdir(parents=True)
                target.write_text("not for distribution")
                with patch.object(build_windows, "source_archive"), self.assertRaisesRegex(ValueError, "Refusing to package"):
                    build_windows.finalize(output, assets, False)

    def test_finalizer_preserves_portuguese_tokenizer_rules_and_hashes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            assets = output / "assets"
            assets.mkdir()
            (assets / "asset-manifest.json").write_text("{}")
            for edition in ("CLI", "GUI"):
                folder = output / f"AutoSubtitlePlus-{edition}"
                rules = folder / "_internal/sacremoses/data/nonbreaking_prefixes/nonbreaking_prefix.pt"
                rules.parent.mkdir(parents=True)
                rules.write_text("Sr\nSra\n")
            with patch.object(build_windows, "source_archive"):
                build_windows.finalize(output, assets, True)
            manifest = json.loads((output / "AutoSubtitlePlus-CLI/manifest.json").read_text())
            entry = next(item for item in manifest["files"] if item["path"].endswith(".pt"))
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertTrue((output / "AutoSubtitlePlus-GUI-Windows-x64.zip.sha256").exists())


if __name__ == "__main__":
    unittest.main()

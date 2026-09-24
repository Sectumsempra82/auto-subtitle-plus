import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_bootstrap, build_windows


class WindowsBuildTests(unittest.TestCase):
    def test_source_archive_contains_rebuild_shortcut_but_no_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.zip"
            build_windows.source_archive(path)
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        self.assertIn("Build Windows.cmd", names)
        self.assertIn("packaging/Build-Windows.ps1", names)
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


class PortableRebuildTests(unittest.TestCase):
    """The builder replaces a used portable folder without touching its deps."""

    @staticmethod
    def _build(output, zip_editions=False):
        def compile_launcher(command, **kwargs):
            target = next(part for part in command if part.startswith("/out:"))
            Path(target[len("/out:"):]).write_bytes(b"MZ stub launcher")
            return subprocess.CompletedProcess(command, 0)

        argv = ["build_bootstrap.py", "--output", str(output)] + ([] if zip_editions else ["--no-zip"])
        with patch.object(build_bootstrap.subprocess, "run", compile_launcher), patch.object(sys, "argv", argv):
            build_bootstrap.main()

    def test_rebuild_reuses_the_folder_keeping_prepared_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            self._build(output)

            prepared = output / "AutoSubtitlePlus-GUI/data/runtimes/abc/python/python.exe"
            prepared.parent.mkdir(parents=True)
            prepared.write_bytes(b"a prepared runtime")
            stale = output / "AutoSubtitlePlus-GUI/app/auto_subtitle_plus/removed_module.py"
            stale.write_text("dropped in a later release")

            self._build(output)

            self.assertEqual(prepared.read_bytes(), b"a prepared runtime")
            self.assertFalse(stale.exists())
            manifest = json.loads((output / "AutoSubtitlePlus-GUI/manifest.json").read_text())
            paths = [item["path"] for item in manifest["files"]]
            self.assertFalse(any(path.startswith("data/") for path in paths))
            self.assertIn("app/auto_subtitle_plus/__init__.py", paths)

    def test_archive_ships_the_payload_without_the_prepared_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            self._build(output)
            prepared = output / "AutoSubtitlePlus-CLI/data/downloads/python.zip"
            prepared.parent.mkdir(parents=True)
            prepared.write_bytes(b"a cached download")

            self._build(output, zip_editions=True)

            with zipfile.ZipFile(output / "AutoSubtitlePlus-CLI-Windows-x64.zip") as archive:
                names = archive.namelist()
            self.assertTrue(prepared.is_file())
            self.assertFalse(any("/data/" in name for name in names), [n for n in names if "/data/" in n])
            self.assertIn("AutoSubtitlePlus-CLI/manifest.json", names)


if __name__ == "__main__":
    unittest.main()

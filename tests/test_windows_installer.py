import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from tools.package_windows import verify_payload


class InstallerPayloadTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows data directory selection")
    def test_installed_data_is_writable_per_user_and_portable_data_stays_local(self):
        source = Path(__file__).resolve().parents[1] / "packaging/Start-Application.ps1"
        script = ("$tokens=$null; $errors=$null; "
                  "$ast=[Management.Automation.Language.Parser]::ParseFile($env:ASP_TEST_SOURCE,[ref]$tokens,[ref]$errors); "
                  "if ($errors.Count) {throw $errors[0]}; "
                  "$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ApplicationDataDirectory'},$true); "
                  ". ([scriptblock]::Create($fn.Extent.Text)); Get-ApplicationDataDirectory $env:ASP_TEST_ROOT")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = os.environ.copy()
            environment.pop("AUTO_SUBTITLE_PLUS_DATA_DIR", None)
            environment.update(ASP_TEST_SOURCE=str(source), ASP_TEST_ROOT=str(root))
            def selected():
                return Path(subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script], env=environment, text=True).strip())
            (root / "installation.ini").write_text("[Application]\nEdition=Portable\n")
            self.assertEqual(selected(), root / "data")
            (root / "installation.ini").write_text("[Application]\nEdition=Installed\n")
            installed = selected()
            self.assertNotEqual(installed, root / "data")
            self.assertIn("AutoSubtitlePlus", installed.parts)
            (root / "data").mkdir()
            self.assertEqual(selected(), root / "data")
            environment["AUTO_SUBTITLE_PLUS_DATA_DIR"] = str(root / "custom")
            self.assertEqual(selected(), root / "custom")

    def payload(self, root: Path) -> None:
        (root / 'launcher.exe').write_bytes(b'reviewed launcher')
        self.manifest(root, 'launcher.exe')

    def manifest(self, root: Path, name: str) -> None:
        (root / 'manifest.json').write_text(json.dumps({
            'version': '0.3.0rc5', 'files': [{'path': name, 'size': 17,
                'sha256': hashlib.sha256(b'reviewed launcher').hexdigest()}],
        }))

    def test_verified_payload_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            self.assertEqual(verify_payload(root)['version'], '0.3.0rc5')

    def test_modified_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            (root / 'launcher.exe').write_bytes(b'changed launcher!')
            with self.assertRaisesRegex(ValueError, 'differs'):
                verify_payload(root)

    def test_user_data_is_never_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.payload(root)
            (root / 'data').mkdir()
            (root / 'data/state.json').write_text('private')
            with self.assertRaisesRegex(ValueError, 'Unexpected files'):
                verify_payload(root)
            self.manifest(root, 'data/state.json')
            with self.assertRaisesRegex(ValueError, 'Invalid payload path'):
                verify_payload(root)

    def test_manifest_cannot_escape_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ('../private', '/private', 'C:/private'):
                if path.startswith('C:') and not Path(path).drive:
                    continue
                with self.subTest(path=path):
                    self.manifest(root, path)
                    with self.assertRaisesRegex(ValueError, 'Invalid payload path'):
                        verify_payload(root)


if __name__ == '__main__':
    unittest.main()

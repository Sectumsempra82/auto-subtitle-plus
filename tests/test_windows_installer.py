import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.package_windows import verify_payload


class InstallerPayloadTests(unittest.TestCase):
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

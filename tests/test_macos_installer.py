import hashlib
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree

from tools.package_macos import APP_NAME, BUNDLE_ID, PACKAGE_ID, build_installer


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS packaging tools")
class MacInstallerTests(unittest.TestCase):
    def test_built_package_replaces_only_the_app_and_preserves_upgrade_guards(self):
        with tempfile.TemporaryDirectory(prefix="asp package test ") as temporary:
            root = Path(temporary)
            app = root / APP_NAME
            executable = app / "Contents/MacOS/Fixture"
            executable.parent.mkdir(parents=True)
            source = root / "fixture.c"
            source.write_text("int main(void) { return 0; }\n")
            subprocess.run(["xcrun", "clang", str(source), "-o", str(executable)], check=True)
            info = {
                "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": "Fixture",
                "CFBundlePackageType": "APPL", "CFBundleVersion": "4",
                "CFBundleShortVersionString": "0.3.0", "LSMinimumSystemVersion": "26.0",
            }
            with (app / "Contents/Info.plist").open("wb") as stream:
                plistlib.dump(info, stream)
            subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
            package = build_installer(app, root / "Installer.pkg")
            expanded = root / "expanded"
            subprocess.run(["pkgutil", "--expand-full", str(package), str(expanded)], check=True)

            component = ElementTree.parse(expanded / "Application.pkg/PackageInfo").getroot()
            self.assertEqual(component.get("identifier"), PACKAGE_ID)
            self.assertEqual(component.get("version"), "4")
            self.assertEqual(component.get("install-location"), "/Applications")
            self.assertEqual(component.get("relocatable"), "false")
            for policy in ("upgrade-bundle", "bundle-version", "strict-identifier"):
                self.assertEqual([bundle.get("id") for bundle in component.findall(f"{policy}/bundle")], [BUNDLE_ID])
            self.assertEqual(component.findall("update-bundle/bundle"), [])
            self.assertEqual(component.findall("relocate/bundle"), [])
            self.assertIsNone(component.find("scripts"))
            payload = expanded / "Application.pkg/Payload"
            self.assertEqual([path.name for path in payload.iterdir()], [APP_NAME])
            self.assertEqual((payload / APP_NAME / "Contents/MacOS/Fixture").read_bytes(), executable.read_bytes())

            distribution = ElementTree.parse(expanded / "Distribution").getroot()
            self.assertEqual(distribution.find("options").get("hostArchitectures"), "arm64")
            self.assertEqual(distribution.find(".//os-version").get("min"), "26.0")
            self.assertEqual(distribution.find("domains").attrib, {
                "enable_localSystem": "true", "enable_currentUserHome": "false", "enable_anywhere": "false",
            })
            self.assertEqual(distribution.find(".//must-close/app").get("id"), BUNDLE_ID)
            self.assertTrue((expanded / "Resources/welcome.html").is_file())
            checksum = hashlib.sha256(package.read_bytes()).hexdigest()
            self.assertEqual(package.with_suffix(".pkg.sha256").read_text(), f"{checksum}  Installer.pkg\n")


if __name__ == "__main__":
    unittest.main()

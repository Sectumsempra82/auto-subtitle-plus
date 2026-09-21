"""Build the standalone Apple Silicon app, manual-update installer and ZIP."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Auto Subtitle Plus.app"
BUNDLE_ID = "local.autosubtitleplus.desktop"
PACKAGE_ID = BUNDLE_ID + ".pkg"


def write_checksum(path: Path) -> None:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n")


def build_installer(app: Path, output: Path) -> Path:
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if app.name != APP_NAME or info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise ValueError("Installer requires the Auto Subtitle Plus application bundle.")
    build_number = str(info["CFBundleVersion"])
    if not build_number.isascii() or not build_number.isdecimal() or int(build_number) < 1:
        raise ValueError("CFBundleVersion must be a positive, increasing build number.")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asp-installer-") as temporary:
        work = Path(temporary)
        payload = work / "payload"
        payload.mkdir()
        subprocess.run(["ditto", str(app), str(payload / APP_NAME)], check=True)
        components = work / "components.plist"
        with components.open("wb") as stream:
            plistlib.dump([{
                "RootRelativeBundlePath": APP_NAME,
                "BundleIsRelocatable": False,
                "BundleIsVersionChecked": True,
                "BundleHasStrictIdentifier": True,
                "BundleOverwriteAction": "upgrade",
            }], stream)
        component = work / "Application.pkg"
        subprocess.run([
            "pkgbuild", "--root", str(payload), "--component-plist", str(components),
            "--identifier", PACKAGE_ID, "--version", build_number,
            "--install-location", "/Applications", str(component),
        ], check=True)
        requirements = work / "requirements.plist"
        with requirements.open("wb") as stream:
            plistlib.dump({"os": [info["LSMinimumSystemVersion"]], "arch": ["arm64"]}, stream)
        distribution = work / "Distribution.xml"
        subprocess.run([
            "productbuild", "--synthesize", "--product", str(requirements),
            "--package", str(component), str(distribution),
        ], check=True)
        tree = ElementTree.parse(distribution)
        root = tree.getroot()
        ElementTree.SubElement(root, "title").text = "Auto Subtitle Plus"
        ElementTree.SubElement(root, "domains", {
            "enable_localSystem": "true", "enable_currentUserHome": "false", "enable_anywhere": "false",
        })
        ElementTree.SubElement(root, "welcome", {"file": "welcome.html", "mime-type": "text/html"})
        package_ref = ElementTree.SubElement(root, "pkg-ref", {"id": PACKAGE_ID})
        must_close = ElementTree.SubElement(package_ref, "must-close")
        ElementTree.SubElement(must_close, "app", {"id": BUNDLE_ID})
        tree.write(distribution, encoding="utf-8", xml_declaration=True)
        subprocess.run([
            "productbuild", "--distribution", str(distribution), "--package-path", str(work),
            "--resources", str(ROOT / "packaging/macos-installer"), str(output),
        ], check=True)
    write_checksum(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-number", type=int, required=True,
                        help="Positive build number, greater than every previous Mac release (rc.4 uses 5)")
    args = parser.parse_args()
    if args.build_number < 1:
        parser.error("--build-number must be positive")
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("Build this package on an Apple Silicon Mac.")
    notices = ROOT / ".build/macos-notices"
    notices.mkdir(parents=True, exist_ok=True)
    sources = ROOT / ".build/macos-source-archives"
    manifest = json.loads((sources / "sources.json").read_text())
    shutil.copy2(sources / "sources.json", notices / "native-sources.json")
    for row in manifest:
        with tarfile.open(sources / row["file"]) as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if member.isfile() and ".." not in parts and not Path(member.name).is_absolute() and any(
                    part.lower().startswith(("license", "copying", "notice", "copyright")) for part in parts
                ):
                    destination = notices / row["name"] / member.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.extractfile(member).read())
    brew = shutil.which("brew")
    if brew:
        prefix = Path(brew).parent.parent / "opt"
        for package in ("openssl@3", "xz", "mpdecimal", "sqlite"):
            for source in (prefix / package).iterdir():
                if source.is_file() and source.name.lower().startswith(("license", "copying", "copyright")):
                    destination = notices / package / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    shutil.copy2(ROOT / "LICENSE", notices / "AutoSubtitlePlus-LICENSE.txt")
    python_license = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    for license_file in (ROOT / "packaging/licenses").glob("*GPL*.txt"):
        shutil.copy2(license_file, notices / license_file.name)
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        inventory.append({"name": name, "version": dist.version,
                          "homepage": dist.metadata.get("Home-page"),
                          "project_urls": dist.metadata.get_all("Project-URL", [])})
        for file in dist.files or []:
            if any(part.lower().startswith(("license", "copying", "notice", "copyright")) for part in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = notices / name / str(file).replace("../", "")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    (notices / "build-environment.json").write_text(json.dumps(inventory, indent=2) + "\n")
    shutil.copy2(ROOT / "packaging/MACOS.txt", notices / "MACOS.txt")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath",
                    str(ROOT / "dist/macos-release"), "--workpath", str(ROOT / ".build/macos-freeze"),
                    str(ROOT / "packaging/macos.spec")], cwd=ROOT, check=True,
                   env={**os.environ, "ASP_MACOS_BUILD_NUMBER": str(args.build_number)})
    app = ROOT / "dist/macos-release/Auto Subtitle Plus.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    archive = ROOT / "dist/AutoSubtitlePlus-GUI-macOS-arm64.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
    write_checksum(archive)
    print(build_installer(app, ROOT / "dist/AutoSubtitlePlus-GUI-macOS-arm64.pkg"))
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

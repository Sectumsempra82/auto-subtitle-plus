"""Build the distributable Apple Silicon application (no checkout or Python required)."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
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
                    str(ROOT / "packaging/macos.spec")], cwd=ROOT, check=True)
    app = ROOT / "dist/macos-release/Auto Subtitle Plus.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    archive = ROOT / "dist/AutoSubtitlePlus-GUI-macOS-arm64.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

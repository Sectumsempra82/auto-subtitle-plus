"""Build model-free Windows x64 portable editions using the tested Python environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from importlib import metadata

ROOT = Path(__file__).resolve().parents[1]
BUILD_TOOLS = {"pyinstaller": "6.21.0", "pyinstaller-hooks-contrib": "2026.6"}
FFMPEG_HASHES = {
    "ffmpeg.exe": "5af82a0d4fe2b9eae211b967332ea97edfc51c6b328ca35b827e73eac560dc0d",
    "ffprobe.exe": "192a1d6899059765ac8c39764fc3148d4e6049955956dc2029f81f4bd6a8972d",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def prepare_assets(directory, ffmpeg_dir, runtime_cache):
    from auto_subtitle_plus.local_translation import ensure_llama_runtime, LLAMA_RELEASE

    directory.mkdir(parents=True, exist_ok=True)
    licenses = directory / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "packaging" / "licenses", licenses / "supplemental", dirs_exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        source = ffmpeg_dir / "bin" / name
        if not source.is_file():
            raise ValueError(f"Missing real FFmpeg distribution binary: {source}")
        if digest(source) != FFMPEG_HASHES[name]:
            raise ValueError(f"FFmpeg binary does not match the pinned 8.0.1 build: {source}")
        copy_file(source, directory / "bin" / name)
    for name in ("LICENSE", "README.txt"):
        copy_file(ffmpeg_dir / name, licenses / "FFmpeg" / name)
    for device in ("cpu", "cuda"):
        executable = ensure_llama_runtime(device, cache_dir=runtime_cache)
        folder = executable.parent
        while not (folder / "receipt.json").exists():
            if folder == folder.parent:
                raise ValueError("Managed llama.cpp receipt not found")
            folder = folder.parent
        shutil.copytree(folder, directory / "runtimes" / f"llama-{LLAMA_RELEASE}-{device}",
                        dirs_exist_ok=True)
    copy_file(ROOT / "LICENSE", licenses / "AutoSubtitlePlus" / "LICENSE")
    copy_file(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python" / "LICENSE.txt")
    distributions = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        records = []
        for item in dist.files or ():
            relative = Path(str(item))
            if any(part == ".." for part in relative.parts):
                continue
            if not any(word in relative.name.lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(dist.locate_file(item))
            if source.is_file() and source.stat().st_size < 10_000_000:
                target = licenses / name / relative
                copy_file(source, target)
                records.append(target.relative_to(licenses).as_posix())
        distributions.append({"name": name, "version": dist.version, "license_files": records,
                              "homepage": dist.metadata.get("Home-page"),
                              "project_urls": dist.metadata.get_all("Project-URL") or []})
    (licenses / "build-environment.json").write_text(
        json.dumps(sorted(distributions, key=lambda item: item["name"].lower()), indent=2), encoding="utf-8")
    ffmpeg_version = subprocess.check_output([str(directory / "bin" / "ffmpeg.exe"), "-version"],
                                              text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    manifest = {"python": sys.version, "build_tools": BUILD_TOOLS,
                "ffmpeg_version": ffmpeg_version, "llama_release": LLAMA_RELEASE,
                "assets": [{"path": path.relative_to(directory).as_posix(), "size": path.stat().st_size,
                            "sha256": digest(path)}
                           for path in sorted(directory.rglob("*")) if path.is_file()]}
    (directory / "asset-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def source_archive(destination):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for base in ("auto_subtitle_plus", "packaging", "tools", "tests"):
            for path in sorted((ROOT / base).rglob("*")):
                if path.is_file() and path.suffix in (".py", ".ps1", ".spec", ".json", ".md", ".txt"):
                    archive.write(path, path.relative_to(ROOT))
        for name in ("setup.py", "LICENSE", "README.md", "Build Portable.cmd"):
            archive.write(ROOT / name, name)


def finalize(output, assets, make_zip):
    for edition in ("CLI", "GUI"):
        folder = output / f"AutoSubtitlePlus-{edition}"
        if not folder.is_dir():
            raise ValueError(f"Missing build: {folder}")
        for name in ("README.md", "LICENSE"):
            copy_file(ROOT / name, folder / name)
        copy_file(ROOT / "packaging" / "PORTABLE.txt", folder / "PORTABLE.txt")
        shutil.copytree(ROOT / "packaging" / "licenses", folder / "_internal" / "licenses" / "supplemental", dirs_exist_ok=True)
        copy_file(assets / "asset-manifest.json", folder / "asset-manifest.json")
        source_archive(folder / "application-source.zip")
        files = []
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            relative = path.relative_to(folder)
            portuguese_prefixes = relative.as_posix() == "_internal/sacremoses/data/nonbreaking_prefixes/nonbreaking_prefix.pt"
            if relative.parts[0] == "data" or path.suffix in (".gguf", ".safetensors") or (path.suffix == ".pt" and not portuguese_prefixes) or path.name == "model.bin":
                raise ValueError(f"Refusing to package model/user data: {relative}")
            files.append({"path": relative.as_posix(), "size": path.stat().st_size, "sha256": digest(path)})
        (folder / "manifest.json").write_text(json.dumps({"edition": edition, "files": files}, indent=2), encoding="utf-8")
        if make_zip:
            archive_path = output / f"AutoSubtitlePlus-{edition}-Windows-x64.zip"
            print(f"Compressing {edition}: {sum(item['size'] for item in files) / 1024**3:.2f} GiB", flush=True)
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        if path.stat().st_size > 100 * 1024**2:
                            print(f"  {path.relative_to(folder)}", flush=True)
                        archive.write(path, Path(folder.name) / path.relative_to(folder))
            checksum = digest(archive_path)
            archive_path.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive_path.name}\n", encoding="ascii")
            print(f"{archive_path}: {archive_path.stat().st_size:,} bytes, SHA256 {checksum}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-dir", type=Path, required=True, help="Unpacked FFmpeg distribution, not a shim")
    parser.add_argument("--runtime-cache", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "windows")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-assets", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--clean", action="store_true", help="Discard PyInstaller analysis caches after dependency changes")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("Build with 64-bit Python on Windows")
    for package, version in BUILD_TOOLS.items():
        if metadata.version(package) != version:
            parser.error(f"Build requires {package}=={version}")
    output = args.output.resolve()
    assets = output / "build-assets"
    output.mkdir(parents=True, exist_ok=True)
    if not args.skip_assets:
        prepare_assets(assets, args.ffmpeg_dir.resolve(), args.runtime_cache)
    if not args.skip_build:
        for edition in ("CLI", "GUI"):
            data = output / f"AutoSubtitlePlus-{edition}" / "data"
            if data.exists():
                raise ValueError(f"Preserve your portable user data outside the build output before rebuilding: {data}")
        env = dict(os.environ, ASP_BUILD_ASSETS=str(assets))
        windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
        env["PATH"] = os.pathsep.join(str(path) for path in (windows / "System32", windows, Path(sys.base_prefix), Path(sys.base_prefix) / "Scripts"))
        log = output / "build.log"
        print(f"Building both editions; log: {log}", flush=True)
        with log.open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", *(["--clean"] if args.clean else []),
                            "--distpath", str(output), "--workpath", str(output / "pyinstaller-work"),
                            str(ROOT / "packaging" / "windows.spec")], cwd=ROOT, env=env,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)
    finalize(output, assets, not args.no_zip)


if __name__ == "__main__":
    main()

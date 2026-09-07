"""App-local, wheel-only dependency acquisition. No host Python or pip required."""
from __future__ import annotations

import argparse
import base64
import builtins
from contextlib import contextmanager
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import urlopen
import uuid
import zipfile

HOSTS = {"files.pythonhosted.org", "download.pytorch.org", "www.python.org", "github.com"}
print = partial(builtins.print, file=sys.stderr)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_extract(archive, destination):
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        source.extractall(destination)


def acquire(item, cache, root, offline=False):
    if "bundled" in item:
        path = (root / item["bundled"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Unsafe bundled path")
        if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise RuntimeError(f"Bundled package checksum mismatch: {path.name}")
        return path
    cache.mkdir(parents=True, exist_ok=True)
    filename = item["filename"]
    if Path(filename).name != filename:
        raise ValueError("Unsafe download filename")
    path = cache / filename
    if path.exists() and path.stat().st_size == item["size"] and digest(path) == item["sha256"]:
        return path
    if offline:
        raise RuntimeError(f"Offline: missing or corrupt download {filename}")
    url = urlparse(item["url"])
    if url.scheme != "https" or url.hostname not in HOSTS:
        raise ValueError("Download must use an approved HTTPS host")
    partial = path.with_name(path.name + ".partial")
    for attempt in range(3):
        try:
            print(f"Downloading {filename} ({item['size'] / 1048576:.1f} MiB)", flush=True)
            with urlopen(item["url"], timeout=60) as response, partial.open("wb") as output:
                count, last = 0, time.monotonic()
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    count += len(chunk)
                    if count > item["size"]:
                        raise RuntimeError("Download exceeded expected size")
                    if time.monotonic() - last > 2:
                        print(f"  {filename}: {count / item['size']:.0%}", flush=True)
                        last = time.monotonic()
            if partial.stat().st_size != item["size"] or digest(partial) != item["sha256"]:
                raise RuntimeError(f"Download checksum mismatch: {filename}")
            partial.replace(path)
            return path
        except (OSError, RuntimeError):
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


@contextmanager
def setup_lock(data):
    import msvcrt
    path = data / "dependencies.lock"
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + 600
        announced = False
        while True:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another dependency setup is still running. Retry later.") from error
                if not announced:
                    print("Waiting for another dependency setup to finish...", flush=True)
                    announced = True
                time.sleep(1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def selected_packages(manifest, edition, device):
    groups = {"core", "installer", device}
    if edition == "GUI":
        groups.add("gui")
    return [item for item in manifest["packages"] if item["group"] in groups]


def runtime_key(manifest, edition, device):
    selected = {"python": manifest["python"], "ffmpeg": manifest["ffmpeg"],
                "packages": selected_packages(manifest, edition, device), "bootstrap_schema": 1}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()[:20]


def valid_runtime(path, full=False):
    try:
        receipt = json.loads((path / "receipt.json").read_text())
        if not receipt["files"]:
            return False
        for index, item in enumerate(receipt["files"]):
            if full and index % 2000 == 0:
                print(f"Verifying installed files: {index}/{len(receipt['files'])}", flush=True)
            relative = Path(item["path"])
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                return False
            if not full and relative.suffix.lower() not in {".exe", ".dll", ".pyd", "._pth"} and relative.name != "METADATA":
                continue
            file = path / relative
            if not file.is_file() or file.stat().st_size != item["size"]:
                return False
            if full and digest(file) != item["sha256"]:
                return False
        return True
    except (OSError, ValueError, KeyError):
        return False


def environment(data, runtime):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "_PYI", "AUTO_SUBTITLE_PLUS_PORTABLE_"))}
    system = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env["PATH"] = os.pathsep.join([str(runtime / "ffmpeg/bin"), str(system / "System32"), str(system)])
    env["AUTO_SUBTITLE_PLUS_DATA_DIR"] = str(data)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def publish_runtime(stage, runtime):
    previous = None
    if runtime.exists():
        previous = runtime.with_name(runtime.name + ".previous-" + uuid.uuid4().hex)
        runtime.rename(previous)
    try:
        stage.rename(runtime)
    except OSError:
        if previous is not None and not runtime.exists():
            previous.rename(runtime)
        raise


def prepare(root, data, manifest, edition, device, offline=False, repair=False):
    packages = selected_packages(manifest, edition, device)
    runtime = data / "runtimes" / runtime_key(manifest, edition, device)
    if not runtime.exists() and runtime.parent.exists():
        for previous in sorted(runtime.parent.glob(runtime.name + ".previous-*"), reverse=True):
            if valid_runtime(previous, full=True):
                previous.rename(runtime)
                print("Recovered the previous complete runtime after interrupted setup.", flush=True)
                break
    if not repair and valid_runtime(runtime):
        return runtime
    cache = data / "downloads"
    items = [manifest["python"], manifest["ffmpeg"], *packages]
    print(f"Preparing {edition} / {device.upper()} dependencies in {data}", flush=True)
    print(f"Selected archives: {sum(item['size'] for item in items) / 1048576:.0f} MiB before cached files; models separate.", flush=True)
    print("No global Python, PATH, registry or system installer changes.", flush=True)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    required = sum(item["size"] for item in items) * 5 + 64 * 1024 * 1024
    if shutil.disk_usage(data).free < required:
        raise RuntimeError(f"Insufficient free disk; allow {required / 1073741824:.1f} GiB for setup staging")
    downloads = {item["filename"]: acquire(item, cache, root, offline) for item in items}
    stage = runtime.with_name(runtime.name + ".pending-" + uuid.uuid4().hex)
    stage.mkdir()
    try:
        safe_extract(downloads[manifest["python"]["filename"]], stage / "python")
        (stage / "python/python313._pth").write_text("python313.zip\n.\n../site\nimport site\n", encoding="ascii")
        installer = next(item for item in packages if item["group"] == "installer")
        sys.path.insert(0, str(downloads[installer["filename"]]))
        from installer import install
        from installer.destinations import SchemeDictionaryDestination
        from installer.sources import WheelFile
        destination = SchemeDictionaryDestination(
            scheme_dict={key: str(stage / folder) for key, folder in {
                "purelib": "site", "platlib": "site", "headers": "Include", "scripts": "Scripts", "data": "."}.items()},
            interpreter=str(runtime / "python/python.exe"), script_kind="win-amd64", bytecode_optimization_levels=[])
        for item in packages:
            if item["group"] == "installer":
                continue
            print(f"Installing {item['name']} {item['version']}", flush=True)
            with WheelFile.open(downloads[item["filename"]]) as wheel:
                install(wheel, destination, additional_metadata={})
        safe_extract(downloads[manifest["ffmpeg"]["filename"]], stage / "ffmpeg-unpack")
        unpacked = stage / "ffmpeg-unpack/ffmpeg-8.0.1-essentials_build"
        unpacked.rename(stage / "ffmpeg")
        (stage / "ffmpeg-unpack").rmdir()
        check = "import torch, whisper, stable_whisper, faster_whisper, ctranslate2, transformers, psutil, sentencepiece; "
        if edition == "GUI":
            check += "from PySide6 import QtCore, QtWidgets; "
        check += "print('Runtime imports verified')"
        subprocess.run([str(stage / "python/python.exe"), "-I", "-c", check], check=True,
                       env=environment(data, stage))
        subprocess.run([str(stage / "ffmpeg/bin/ffmpeg.exe"), "-version"], check=True,
                       stdout=subprocess.DEVNULL, env=environment(data, stage))
        print("Recording installed-file integrity checksums...", flush=True)
        files = []
        for file in sorted(stage.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                files.append({"path": str(file.relative_to(stage)), "size": file.stat().st_size, "sha256": digest(file)})
                if len(files) % 2000 == 0:
                    print(f"  Verified {len(files)} installed files", flush=True)
        (stage / "receipt.json").write_text(json.dumps({"files": files, "device": device, "edition": edition}))
        publish_runtime(stage, runtime)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return runtime


def main():
    root = Path(__file__).resolve().parent
    data = Path(os.environ.get("AUTO_SUBTITLE_PLUS_DATA_DIR", root / "data")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    raw = json.loads(base64.b64decode(os.environ.get("ASP_ARGUMENTS", "W10=")).decode("utf-8"))
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--runtime-device", choices=("cpu", "cuda"))
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--check-dependencies", action="store_true")
    parser.add_argument("--repair-dependencies", action="store_true")
    options, arguments = parser.parse_known_args(raw)
    offline = "--offline" in arguments
    manifest = json.loads((root / "dependencies-windows.json").read_text(encoding="utf-8"))
    profile = data / "runtime-profile.json"
    device = options.runtime_device or (json.loads(profile.read_text())["device"] if profile.exists() else "cpu")
    edition = os.environ.get("ASP_EDITION", "CLI")
    if options.check_dependencies:
        runtime = data / "runtimes" / runtime_key(manifest, edition, device)
        ready = valid_runtime(runtime, full=True)
        print(json.dumps({"ready": ready, "edition": edition, "device": device, "runtime": str(runtime)}), file=sys.stdout, flush=True)
        return 0 if ready else 2
    runtime = data / "runtimes" / runtime_key(manifest, edition, device)
    if options.repair_dependencies or options.runtime_device or not valid_runtime(runtime):
        with setup_lock(data):
            runtime = prepare(root, data, manifest, edition, device, offline, options.repair_dependencies)
            pending = profile.with_suffix(".pending")
            pending.write_text(json.dumps({"device": device}))
            pending.replace(profile)
    if options.setup_only:
        print("Dependency setup complete.", flush=True)
        return 0
    print("ASP_READY", flush=True)
    print(f"Runtime profile: {device.upper()}. Change with --runtime-device cpu|cuda --setup-only.", flush=True)
    return subprocess.call([str(runtime / "python/python.exe"), "-I", str(root / "app_entry.py"), edition, *arguments],
                           env=environment(data, runtime))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Dependency setup failed: {error}\nNo system-wide installation was attempted. Retry or use --repair-dependencies.\n"
              "For a missing Microsoft runtime/driver, obtain it from Microsoft/NVIDIA only after explicit approval.", file=sys.stderr, flush=True)
        raise SystemExit(1)

"""Build small native Windows launchers and model-free application payloads."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/windows-light")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()
    catalog = ROOT / "packaging/dependencies-windows.json"
    if not catalog.is_file():
        raise SystemExit("Run tools/lock_bootstrap.py to create the reviewed dependency catalog first")
    manifest = json.loads(catalog.read_text(encoding="utf-8"))
    setup = next(node for node in ast.walk(ast.parse((ROOT / "setup.py").read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setup")
    version = ast.literal_eval(next(item.value for item in setup.keywords if item.arg == "version"))
    compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for edition in ("CLI", "GUI"):
        folder = output / f"AutoSubtitlePlus-{edition}"
        if folder.exists():
            if (folder / "data").exists():
                raise ValueError(f"Refusing to rebuild a used portable folder: {folder}. Choose another --output")
            if not folder.resolve().is_relative_to(output):
                raise ValueError("Output resolves outside build directory")
            shutil.rmtree(folder)
        folder.mkdir()
        shutil.copytree(ROOT / "auto_subtitle_plus", folder / "app/auto_subtitle_plus",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("bootstrap.py", "app_entry.py", "Start-Application.ps1", "dependencies-windows.json", "gui_smoke.py"):
            shutil.copy2(ROOT / "packaging" / name, folder / name)
        for item in manifest["packages"]:
            if "bundled" in item:
                source = ROOT / "packaging" / item["bundled"]
                if digest(source) != item["sha256"]:
                    raise ValueError(f"Bundled wheel differs from lock: {source}")
                target = folder / item["bundled"]
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
        shutil.copy2(ROOT / "LICENSE", folder / "LICENSE")
        shutil.copy2(ROOT / "packaging/PORTABLE.txt", folder / "README.txt")
        shutil.copy2(ROOT / "README.md", folder / "PROJECT-README.md")
        shutil.copytree(ROOT / "assets/screenshots", folder / "assets/screenshots")
        (folder / "packaging").mkdir()
        shutil.copy2(ROOT / "packaging/README.md", folder / "packaging/README.md")
        release_notes = ROOT / f"packaging/RELEASE-v{version.replace('rc', '-rc.')}.md"
        shutil.copy2(release_notes, folder / "packaging" / release_notes.name)
        shutil.copytree(ROOT / "packaging/licenses", folder / "licenses")
        executable = folder / ("auto_subtitle_plus_gui.exe" if edition == "GUI" else "auto_subtitle_plus.exe")
        command = [str(compiler), "/nologo", "/optimize+", "/platform:x64", "/r:System.Web.Extensions.dll",
                   "/out:" + str(executable)]
        if edition == "GUI":
            command += ["/define:GUI", "/target:winexe", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"]
        command.append(str(ROOT / "packaging/Launcher.cs"))
        subprocess.run(command, check=True)
        for name, flags in {"Setup CPU.cmd": "--runtime-device cpu --setup-only", "Setup CUDA.cmd": "--runtime-device cuda --setup-only",
                            "Check Dependencies.cmd": "--check-dependencies", "Repair Dependencies.cmd": "--repair-dependencies --setup-only"}.items():
            (folder / name).write_text(f'@echo off\r\n"%~dp0{executable.name}" {flags} %*\r\npause\r\n', encoding="ascii")
        files = [{"path": path.relative_to(folder).as_posix(), "size": path.stat().st_size, "sha256": digest(path)}
                 for path in sorted(folder.rglob("*")) if path.is_file()]
        (folder / "manifest.json").write_text(json.dumps({"version": version, "edition": edition, "files": files}, indent=2))
        print(f"{edition} payload: {sum(item['size'] for item in files) / 1048576:.2f} MiB", flush=True)
        if not args.no_zip:
            archive = output / f"AutoSubtitlePlus-{edition}-Windows-x64.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        stream.write(path, path.relative_to(output))
            archive.with_suffix(".zip.sha256").write_text(digest(archive) + "  " + archive.name + "\n")
            print(f"Archive: {archive} ({archive.stat().st_size / 1048576:.2f} MiB)", flush=True)


if __name__ == "__main__":
    main()

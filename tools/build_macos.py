"""Build a local Finder launcher backed by this checkout's Python environment."""

import argparse
import json
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

LAUNCHER_SOURCE = r'''
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (chdir(ASP_ROOT) != 0 || access(ASP_PYTHON, X_OK) != 0) {
        fputs("The checkout or .venv moved. Rebuild the Mac launcher.\n", stderr);
        return 1;
    }
    const char *old_path = getenv("PATH");
    char *path = NULL;
    if (asprintf(&path, "%s:%s", ASP_FFMPEG, old_path ? old_path : "/usr/bin:/bin") < 0)
        return 1;
    if (setenv("PATH", path, 1) != 0) { free(path); return 1; }
    free(path);
    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (!args) return 1;
    args[0] = ASP_PYTHON;
    args[1] = "-m";
    args[2] = "auto_subtitle_plus.desktop";
    for (int i = 1; i < argc; i++) args[i + 2] = argv[i];
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    PyStatus status = PyConfig_SetBytesString(&config, &config.executable, ASP_PYTHON);
    if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc + 2, args);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    free(args);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    return Py_RunMain();
}
'''


def build_app(root: Path, python: Path, destination: Path) -> Path:
    # Keep the venv path: resolving its symlink would bypass the environment.
    python = python.absolute()
    root = root.resolve()
    if not python.is_file():
        raise ValueError(f"Python environment not found: {python}. Install .[gui,faster] in .venv first.")
    subprocess.run(
        [str(python), "-c", "import PySide6.QtWidgets, ffmpeg, psutil, stable_whisper, faster_whisper"],
        check=True,
    )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        raise ValueError("FFmpeg and ffprobe must be installed before building the Mac launcher.")
    config = json.loads(subprocess.run(
        [str(python), "-c", "import json,sysconfig; print(json.dumps(dict(include=sysconfig.get_path('include'), **{k: sysconfig.get_config_var(k) for k in ('LIBPL','LDVERSION','LIBS','SYSLIBS')})))"],
        capture_output=True, text=True, check=True,
    ).stdout)
    app = destination / "Auto Subtitle Plus.app"
    executable_dir = app / "Contents/MacOS"
    executable_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "CFBundleName": "Auto Subtitle Plus",
        "CFBundleDisplayName": "Auto Subtitle Plus",
        "CFBundleIdentifier": "local.autosubtitleplus.desktop",
        "CFBundleExecutable": "AutoSubtitlePlus",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.3.0",
        "CFBundleVersion": "3",
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Audio and Video",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.audio", "public.movie"],
        }],
    }
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(metadata, stream)
    launcher = executable_dir / "AutoSubtitlePlus"
    definitions = {"ASP_ROOT": str(root), "ASP_PYTHON": str(python), "ASP_FFMPEG": str(Path(ffmpeg).parent)}
    with tempfile.TemporaryDirectory(prefix="asp-mac-build-") as temporary:
        source = Path(temporary) / "launcher.c"
        source.write_text("".join(f"#define {key} {json.dumps(value)}\n" for key, value in definitions.items()) + LAUNCHER_SOURCE)
        subprocess.run([
            "xcrun", "clang", "-O2", "-Wall", "-Wextra", str(source),
            "-I" + config["include"], "-L" + config["LIBPL"], "-lpython" + config["LDVERSION"],
            *shlex.split(config["LIBS"] or ""), *shlex.split(config["SYSLIBS"] or ""),
            "-o", str(launcher),
        ], check=True)
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/macos")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Build the local Mac launcher on macOS.")
    try:
        print(build_app(ROOT, args.python, args.output.resolve()))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Cannot build Mac launcher: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

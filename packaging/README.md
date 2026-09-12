# Lightweight Windows Editions

The default delivery is now a small native launcher and application payload,
not the superseded 8.6 GiB PyInstaller bundle. Dependencies come from pinned
public upstream archives; preparation is app-local and needs no administrator.
Distribution size and installed runtime size are different: CUDA still uses
several GiB when explicitly selected. Models remain separate.

## Rebuild Updated Code

Double-click `Build Portable.cmd`, or run from the repository root:

```powershell
.\packaging\Build-Portable.ps1
.\packaging\Build-Portable.ps1 -PythonExe C:\path\to\python.exe -Output C:\builds\new-version
```

Use the tested Python 3.13 application development environment for the full
regression suite. Building launchers uses the Windows .NET Framework C# compiler;
it does not need PyInstaller, CUDA SDK, FFmpeg or model downloads. The checked-in
catalog and two vendor wheels are reused, so ordinary code rebuilds do not
resolve new dependency versions. `-SkipTests` is a development-only bypass;
`-NoZip` omits compression. Output is `dist/windows-light` with CLI/GUI folders,
ZIPs, per-file manifests and ZIP SHA256 files. A folder containing user `data`
is never overwritten by a rebuild; choose a different output directory.

## Update Dependency Pins

README screenshots can be refreshed with `python tools/capture_readme_screenshots.py`.
It renders the actual Qt window with example filenames and isolated temporary state;
it does not open or process private media. Screenshots ship with the packaged README.

This is a deliberate release operation, not part of normal rebuilds:

```powershell
python tools/lock_bootstrap.py --pip-python C:\path\to\build-venv\Scripts\python.exe
```

Run using the tested Windows x64 Python 3.13 environment after reviewing
`requirements-windows.txt` and its installed dependency graph. The build venv
needs pip, setuptools and wheel to build the two source-only packages. The
generator validates installed dependency constraints and records exact public
wheel URLs/hashes/size/license metadata. PyTorch comes from its official CDN;
PyPI packages come from files.pythonhosted.org. Python comes from python.org;
FFmpeg from the pinned Gyan GitHub release. Review generated
`dependencies-windows.json` and vendor wheel changes before distributing.
This is a pinned tested-environment lock, not an automatic upgrade policy.

## Destination Setup

The native EXE invokes the supplied PowerShell script, which checks/downloads
official embedded Python. A standard-library bootstrap downloads the selected
wheel set, verifies SHA256 and uses PyPA installer, not pip, to install it in a
staging folder. Imports/FFmpeg are checked before publication. Unfinished setup
does not count as installed; downloads use partial files and retry three times.
Preparation is serialized by an app-local lock and checks disk headroom.

Initial profile: CPU. CLI does not download Qt. GUI downloads Qt. CUDA is an
explicit opt-in via `Setup CUDA.cmd` or `--runtime-device cuda --setup-only`;
it includes the existing tested PyTorch CUDA 11.8 and CTranslate2 CUDA 12
dependencies. There is no automatic global driver/VC++ redist installation.
Missing OS prerequisites require an explicit separate approval and official
Microsoft/NVIDIA installers. CPU mode is not proof every CPU supports every
native library; validate on the destination hardware.

`Check Dependencies.cmd` performs a full installed-file integrity check and
never downloads. Normal startup checks receipt/native-binary and package-metadata
file sizes; the explicit checker hashes every installed file. Use `Repair
Dependencies.cmd` after same-size corruption or an import failure. Setup failures
leave existing runtimes intact. A recovered runtime preserves the old folder for
manual removal after closing all app processes. Shared `AUTO_SUBTITLE_PLUS_DATA_DIR`
reuses download/model caches across editions, although each edition/profile has
its own runtime to avoid modifying active processes. Cached archives are retained.

GUI setup displays download/install progress and supports cancellation of only
its owned process tree. CLI exposes the same setup messages in the terminal.
`--offline` prevents runtime acquisition and reuses verified local archives.
Use `--setup-only --offline` to prepare from a transferred download cache.
No package resolution, compilation, arbitrary remote scripts or global pip
commands run on the destination. No model is silently changed by setup.

## Validation And Credits

Development-host evidence for the lightweight CPU editions: fresh official
dependency acquisition, native import/FFmpeg checks, cached offline CLI launch,
real Faster-Whisper transcription plus OPUS-MT French SRT/TXT export, and compiled
GUI real-window and completed file-queue smoke tests. CLI dependency archives total approximately 472 MiB;
prepared CPU runtime files approximately 1.91 GiB, excluding models and retained
archives. GPU bootstrap acquisition and clean-Windows VM validation remain pending;
earlier full-bundle GPU tests are not a substitute for those new-package gates.

```powershell
python -m unittest discover -s tests -q
.\dist\windows-light\AutoSubtitlePlus-CLI\auto_subtitle_plus.exe --setup-only
.\dist\windows-light\AutoSubtitlePlus-CLI\auto_subtitle_plus.exe --check-dependencies
.\dist\windows-light\AutoSubtitlePlus-CLI\auto_subtitle_plus.exe --offline --help
```

Verify a fresh app-local installation, cached offline launch, both frontends,
real transcription and translation, and explicit GPU setup before a release.
Clean Windows VM validation remains a separate gate; this development machine
already has Microsoft runtimes and NVIDIA drivers. Do not call a local smoke
test clean-machine certification. Model quality gates remain unchanged.

`PORTABLE.txt`, the project README and catalog credit upstreams. PyPA installer
is newly used (MIT). Whisper/Stable-TS wheels preserve their upstream licenses.
Public redistribution of prepared data requires a separate license review.
The old `tools/build_windows.py` / `windows.spec` are retained only as the
superseded full-bundle validation path; the rebuild shortcut no longer calls them.

## macOS Apple Silicon

The Mac preview uses a bundled `.app`, independently of the Windows bootstrap ZIPs. See [Mac packaging and validation](macos.md) and [release notes](RELEASE-v0.3.0-rc.3.md). FFmpeg remains a separate prerequisite.

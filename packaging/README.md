# Lightweight Windows Edition

Windows ships as a single native EXE, not the superseded 8.6 GiB PyInstaller
bundle and not two separate portable/installer downloads. Dependencies come
from pinned public upstream archives; preparation is app-local and needs no
administrator. Distribution size and installed runtime size are different:
CUDA still uses several GiB when explicitly selected. Models remain separate.

## The single EXE

Running `AutoSubtitlePlus-Windows-x64.exe` asks:

1. **Install** (Start Menu shortcut, optional desktop shortcut, uninstaller)
   or **Portable** (copy the application files only — nothing else on the
   computer changes, e.g. for a USB drive or a folder you manage yourself).
2. A destination folder, defaulting to `%ProgramFiles%\Auto Subtitle Plus`
   for either choice; browse to pick anywhere else (portable users typically
   pick a removable drive or a plain local folder instead).
3. If that folder already has content, whether to update/overwrite it in
   place.

That's it — there is no separate "installer" download and no separate "ZIP"
download. Because the default destination is Program Files, Setup always
requests administrator elevation (one UAC prompt), even for Portable, which
only ever copies files into the folder you chose.

`Build Windows.cmd` runs the regression suite, builds both launcher payloads
and compiles the single EXE plus its SHA256. Equivalent:

```powershell
.\packaging\Build-Windows.ps1 -Output dist/windows-rc10
```

Inno Setup 6.7.3 is prepared in portable mode under `.build/installer-tools` from
the official release. Its pinned SHA256 and publisher signature are checked
before extraction. No system-wide compiler installation is required. Pass
`-InstallerCompiler C:\path\ISCC.exe` to use an existing compiler. Pass
`-PayloadOnly` to stop after building the two internal CLI/GUI launcher
folders (for local inspection) without compiling the EXE, or `-KeepPayload`
to keep those folders alongside the compiled EXE instead of deleting them.

Install mode has a stable AppId, registration, remembered destination,
version guards and running-application guards. Only `app/auto_subtitle_plus` is
removed before copying the new code, so obsolete modules and bytecode cannot
survive updates. `data` and exported files are never installer-owned. Existing
copies (portable or installed) select their existing application folder; other
copies are untouched. Application-code junctions/symlinks and unrelated
nonempty destinations are rejected regardless of Install/Portable choice.

Actual upgrade validation uses disposable folders and refuses to run when a daily
installer installation is registered. It tests portable migration, upgrades to the
registered folder, reinstall, downgrade rejection, running-app rejection, stale
module cleanup, user-data preservation and uninstall. A separately compiled older
fixture installer is required; do not substitute a production user installation.
It must run elevated, since Install mode always registers under `HKEY_LOCAL_MACHINE`.

```powershell
python tools/test_windows_installer.py --installer dist/windows-rc10/AutoSubtitlePlus-Windows-x64.exe --older-installer path/to/older-fixture.exe --portable path/to/old-portable-folder --output .build/installer-validation
```

The wrapper supplies Windows fonts to Qt offscreen tests through `QT_QPA_FONTDIR`.
For a direct test run set it to `C:\Windows\Fonts` first; otherwise Qt may substitute
box glyphs and produce false layout failures.

## Rebuild Updated Code

Double-click `Build Windows.cmd`, or run from the repository root:

```powershell
.\packaging\Build-Windows.ps1
.\packaging\Build-Windows.ps1 -PythonExe C:\path\to\python.exe -Output C:\builds\new-version
```

Use the tested Python 3.13 application development environment for the full
regression suite. Building launchers uses the Windows .NET Framework C# compiler;
it does not need PyInstaller, CUDA SDK, FFmpeg or model downloads. The checked-in
catalog and two vendor wheels are reused, so ordinary code rebuilds do not
resolve new dependency versions. `-SkipTests` is a development-only bypass.
Output is `dist/windows-light` containing only `AutoSubtitlePlus-Windows-x64.exe`
and its SHA256 (the two internal CLI/GUI launcher folders used to build it are
removed unless `-KeepPayload` is passed). A folder containing user `data` is
never overwritten by a rebuild; choose a different output directory.

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

These launcher smoke commands target the internal CLI payload folder, so build
with `-KeepPayload` (or `-PayloadOnly`) first:

```powershell
python -m unittest discover -s tests -q
.\packaging\Build-Windows.ps1 -SkipTests -KeepPayload
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

The Mac desktop uses a bundled `.app`, independently of the Windows bootstrap ZIPs. Release `v0.3.0-rc.9` added the redesigned GUI on Mac; the Windows GUI now shows the same workspace. See [Mac packaging and validation](macos.md) and [release notes](RELEASE-v0.3.0-rc.9.md). FFmpeg remains a separate prerequisite.

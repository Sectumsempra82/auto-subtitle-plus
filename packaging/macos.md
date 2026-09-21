# macOS release packaging

The v0.3.0-rc.8 Apple Silicon `.app` bundles Python, Qt and the processing libraries. It does not depend on a checkout or virtual environment. FFmpeg/ffprobe must be installed separately; models download on demand. No media, settings, model weights or credentials belong in the bundle.

Requires macOS 26+ and Apple Silicon. This build was validated on macOS 27.0; Intel and other macOS versions are unvalidated. The bundle is ad-hoc signed, not Developer ID signed or notarized. See [first-launch instructions](MACOS.txt).

## Build

On Apple Silicon, install Python 3.12 and FFmpeg, then from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[gui,faster]'
.venv/bin/python -m pip install 'pyinstaller==6.22.2'
.venv/bin/python tools/package_macos_sources.py
.venv/bin/python tools/package_macos.py --build-number 6
```

Outputs:

- `dist/macos-release/Auto Subtitle Plus.app`
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.pkg` (recommended manual installer/update)
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.pkg.sha256`
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.zip`
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.zip.sha256`
- `dist/AutoSubtitlePlus-macOS-NativeSources.tar` and its SHA-256 file

Use a positive, monotonically increasing `--build-number` for every distributed
Mac build, including rebuilds and release candidates. Existing ZIP releases use
build 3; build 4 was a local installer prototype; rc.4 used build 5. The rc.7 release uses build 6. The number becomes the app's
`CFBundleVersion` and the installer receipt version. Keep increasing it across
marketing-version changes; do not reuse or reset it. Update the app's marketing
version in `macos.spec` when preparing a new version as before.

The ZIP preserves bundle symlinks. The builder verifies the app's ad-hoc signature and collects dependency license files and an environment inventory under `Contents/Resources/licenses`. Inspect the inventory when changing dependency versions. PyInstaller's [Mac packaging notes](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support) explain architecture and signing.

This release uses Homebrew Python 3.12. Its bundled OpenSSL and xz libraries require macOS 26. The source archive includes pinned Qt/PySide and PyAV/FFmpeg codec sources and upstream build recipes. PyAV's FFmpeg build includes GPL codecs: preserve the GPLv3 notices and publish the matching source archive beside the executable. The application source retains its MIT license; the combined binary distribution includes GPL/LGPL components. The separate FFmpeg command-line prerequisite is not in the app. When updating dependencies, update `macos-sources.json`, verify native deployment targets, and collect matching licenses for any new native libraries.

Qt and PySide6 remain dynamically linked. Their LGPL/GPL notices and matching source links are included in [MACOS.txt](MACOS.txt). To replace these libraries, rebuild the app from source or replace the frameworks with compatible builds, then ad-hoc sign the modified bundle with `codesign --force --deep --sign - 'Auto Subtitle Plus.app'`. Do not represent a modified app as the original release.

`tools/build_macos.py` and `Launch Mac.command` are developer conveniences backed by the current checkout and `.venv`; they do not produce the distributable release.

## Manual installation and upgrades

Publish the `.pkg` and its checksum alongside the ZIP and required native sources.
The website prefers a PKG when the newest Mac release contains one and falls back
to ZIP for older releases. Static download links point to the rc.7 installer, so they also work when the
GitHub release API is unavailable.

The user downloads the PKG, finishes their current job, quits the app and follows
macOS Installer. The destination is always `/Applications/Auto Subtitle Plus.app`;
Installer may request administrator authorization. It also supports replacing the
same-named app from an earlier ZIP installation without an existing PKG receipt.
There is no app updater, background service, network request or custom install script.

The component uses a stable package ID (`local.autosubtitleplus.desktop.pkg`),
strict bundle identity, version checking, relocation disabled and the native
`upgrade` replacement policy. Replacement removes obsolete bundle files instead
of merging them. A newer installed bundle is protected from an older bundle;
reinstalling the same build is allowed. Other copies in Downloads or user-selected
folders are not searched for, moved or deleted. The GUI installer declares the app
as `must-close`; command-line administrators must quit it before running Installer.

The payload contains only the app bundle. Settings/queue in Application Support,
model caches, FFmpeg and exported media remain outside the installation. The
installer does not uninstall or reset them. The ZIP remains available for users
who prefer Finder's drag-and-Replace flow.

The package is currently unsigned and the app is ad-hoc signed, not notarized.
Packaging does not remove macOS security prompts. Users should verify the checksum
and trust the source before using Privacy & Security → Open Anyway if blocked.
Developer ID Installer signing and notarization require separate release setup.

Implementation references: local `man pkgbuild` (component property list / upgrade)
and [Apple's Distribution XML reference](https://developer.apple.com/library/archive/documentation/DeveloperTools/Reference/DistributionDefinitionRef/Chapters/Distribution_XML_Ref.html)
(native close-app requirement and installation domains).

## Validate

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -p test_macos_desktop.py
codesign --verify --deep --strict 'dist/macos-release/Auto Subtitle Plus.app'
```

Extract the release ZIP to a different directory, use a minimal PATH and launch the extracted binary. A diagnostic smoke mode opens the actual GUI with isolated state and writes a report/screenshot:

```sh
'/path/to/Auto Subtitle Plus.app/Contents/MacOS/AutoSubtitlePlus' \
  --portable-smoke /tmp/asp-smoke/report.json
```

For an offline transcription test, first cache Whisper `tiny`, then pass `--smoke-run --smoke-media /path/to/speech.wav --smoke-asr-model tiny --smoke-backend stable --smoke-no-translation --smoke-output-dir /tmp/asp-smoke/outputs --smoke-timeout-ms 120000`. The default diagnostic translation test uses Faster-Whisper and OPUS en→fr; prepare their models separately. Use nonprivate sample media and a new output directory.

The GUI and both worker subprocess boundaries must function without the source checkout or `.venv`. Check all five settings tabs at minimum/default/large sizes in both appearances. A release on one developer Mac does not establish clean-machine or every-model certification.

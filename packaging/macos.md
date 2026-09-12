# macOS release packaging

The v0.3.0-rc.3 Apple Silicon `.app` bundles Python, Qt and the processing libraries. It does not depend on a checkout or virtual environment. FFmpeg/ffprobe must be installed separately; models download on demand. No media, settings, model weights or credentials belong in the bundle.

Requires macOS 26+ and Apple Silicon. Validated on macOS 26.5; Intel and other macOS versions are unvalidated. The bundle is ad-hoc signed, not Developer ID signed or notarized. See [first-launch instructions](MACOS.txt).

## Build

On Apple Silicon, install Python 3.12 and FFmpeg, then from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[gui,faster]'
.venv/bin/python -m pip install 'pyinstaller==6.22.2'
.venv/bin/python tools/package_macos_sources.py
.venv/bin/python tools/package_macos.py
```

Outputs:

- `dist/macos-release/Auto Subtitle Plus.app`
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.zip`
- `dist/AutoSubtitlePlus-GUI-macOS-arm64.zip.sha256`
- `dist/AutoSubtitlePlus-macOS-NativeSources.tar` and its SHA-256 file

The ZIP preserves bundle symlinks. The builder verifies the app's ad-hoc signature and collects dependency license files and an environment inventory under `Contents/Resources/licenses`. Inspect the inventory when changing dependency versions. PyInstaller's [Mac packaging notes](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support) explain architecture and signing.

This release uses Homebrew Python 3.12. Its bundled OpenSSL and xz libraries require macOS 26. The source archive includes pinned Qt/PySide and PyAV/FFmpeg codec sources and upstream build recipes. PyAV's FFmpeg build includes GPL codecs: preserve the GPLv3 notices and publish the matching source archive beside the executable. The application source retains its MIT license; the combined binary distribution includes GPL/LGPL components. The separate FFmpeg command-line prerequisite is not in the app. When updating dependencies, update `macos-sources.json`, verify native deployment targets, and collect matching licenses for any new native libraries.

Qt and PySide6 remain dynamically linked. Their LGPL/GPL notices and matching source links are included in [MACOS.txt](MACOS.txt). To replace these libraries, rebuild the app from source or replace the frameworks with compatible builds, then ad-hoc sign the modified bundle with `codesign --force --deep --sign - 'Auto Subtitle Plus.app'`. Do not represent a modified app as the original release.

`tools/build_macos.py` and `Launch Mac.command` are developer conveniences backed by the current checkout and `.venv`; they do not produce the distributable release.

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

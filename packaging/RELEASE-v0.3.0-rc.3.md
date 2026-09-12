# v0.3.0-rc.3 — macOS desktop preview

A native Apple Silicon desktop app, with Python and processing libraries bundled. This is a **Mac release candidate**. Existing Windows GUI/CLI downloads remain at [v0.3.0-rc.2](https://github.com/Sectumsempra82/auto-subtitle-plus/releases/tag/v0.3.0-rc.2); they have not been rebuilt for this release.

## Install on Mac

1. Download `AutoSubtitlePlus-GUI-macOS-arm64.zip` and its `.sha256` file. Verify with `shasum -a 256 -c AutoSubtitlePlus-GUI-macOS-arm64.zip.sha256` from the download folder.
2. Extract the ZIP and move `Auto Subtitle Plus.app` to Applications.
3. Install FFmpeg and ffprobe separately (`brew install ffmpeg` for Homebrew users).
4. Launch the app, add a short local file, select CPU and a small speech model, and start the queue. Models download when first selected; prepared models can run offline.

Requires **Apple Silicon and macOS 26+**. The bundled Homebrew Python dependencies set that minimum. No separate Python installation, account or cloud service is needed. The app is ad-hoc signed, **not Developer ID signed or notarized**. If macOS blocks it, use Privacy & Security → Open Anyway only after verifying the download and deciding you trust it. Do not disable Gatekeeper.

[Mac setup and integrated user guide](https://sectumsempra82.github.io/auto-subtitle-plus/guide/#macos) · [Website](https://sectumsempra82.github.io/auto-subtitle-plus/) · [Build instructions](https://github.com/Sectumsempra82/auto-subtitle-plus/blob/v0.3.0-rc.3/packaging/macos.md)

## Changes

- Native Mac tabs and light/dark appearance; fixes clipped tab labels and stale colors after appearance changes.
- Finder media opening, Mac menus and Command shortcuts, and an empty-queue prompt.
- Local-only Mac translation; M2M100-418M default. CUDA and Windows-only Hy-MT2 are rejected before jobs start.
- Queue filenames stay on one line with full-path tooltips; redundant output labels removed.
- Failed batches report errors instead of a successful 100% completion. Retry and menu/button state remain consistent.
- Mac queue/settings stored in `~/Library/Application Support/AutoSubtitlePlus/desktop/state.json`, with fallback from the old cache location. Upgrading the app preserves user data.
- README and the existing GitHub Pages website/guide updated with Mac downloads, setup and platform-specific release links.

The shared transcription, translation and export calculations are unchanged.

## Validation and limits

- Python regression suite: 218 tests, 2 platform-specific skips.
- Native Cocoa appearance/launcher checks: all 6 passed; five settings tabs checked at three window sizes in light and dark modes.
- Release ZIP extracted outside the checkout and run with a minimal environment: actual GUI, offline Whisper tiny CPU transcription, SRT/TXT output, and OPUS English→French translation passed, including worker subprocesses.
- Bundle ad-hoc signature verified. Native dependencies resolve inside the app or macOS system libraries; no external Homebrew runtime linkage.
- Website local links, anchors, release selection/offline fallback, desktop and mobile layouts checked.

Validated on macOS 26.5 Apple Silicon, not a clean-machine certification. Intel Macs, pre-26 macOS, Apple GPU acceleration, and every model/output combination are unvalidated or unsupported. Model weights and FFmpeg command-line tools are not included. Review generated subtitles before use.

## Source and notices

The app includes third-party notices and a dependency inventory. PyAV includes FFmpeg libraries with GPL codecs; Qt/PySide and other libraries retain their licenses. `AutoSubtitlePlus-macOS-NativeSources.tar` supplies matching Qt/PySide, PyAV/FFmpeg codec sources and upstream build scripts, with a checksum. The project source remains MIT licensed; the combined GPL-covered binary distribution is supplied under GPLv3 terms. The tagged application source and its build scripts are available below.

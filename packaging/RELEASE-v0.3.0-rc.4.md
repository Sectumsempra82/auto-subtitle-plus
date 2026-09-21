# v0.3.0-rc.4 — Mac installer and text transcription

Subtitles remain the primary workflow; plain-text transcription is now easier to discover in the desktop, website and user guide. This **Apple Silicon Mac release candidate** also adds a native installer for manual updates.

Windows GUI/CLI binaries remain at [v0.3.0-rc.2](https://github.com/Sectumsempra82/auto-subtitle-plus/releases/tag/v0.3.0-rc.2). They have not been rebuilt for this release; the shared GUI changes are available in source and the new Mac build.

## Install or update on Mac

1. Download `AutoSubtitlePlus-GUI-macOS-arm64.pkg` and its `.sha256` file. In the download folder, verify with `shasum -a 256 -c AutoSubtitlePlus-GUI-macOS-arm64.pkg.sha256`.
2. Finish any running job and quit Auto Subtitle Plus. Open the PKG and follow macOS Installer; administrator authorization may be required.
3. The installer targets `/Applications/Auto Subtitle Plus.app`. It is configured to replace the whole app bundle, including a same-named copy originally installed from ZIP, while keeping settings, queue, models and exported files in their existing locations. No uninstall is needed.
4. Install FFmpeg/ffprobe separately if necessary (`brew install ffmpeg` for Homebrew users), then open the app from Applications.

A ZIP and matching checksum remain available for drag-and-drop installation. Extract the ZIP, drag the app into Applications and choose **Replace**, not Keep Both. Other app copies outside `/Applications` are not removed.

Requires **Apple Silicon and macOS 26+**. Python and processing libraries are bundled; FFmpeg command-line tools and model weights are not. Updates are manual: there is no update checker, background updater or automatic installation of dependencies.

The **installer is unsigned** and the app is **ad-hoc signed, not Developer ID signed or notarized**. If macOS blocks the installer or app, verify the download and source first. Only if you trust it, use Privacy & Security → Open Anyway after attempting to open it. Do not disable Gatekeeper.

[Website and downloads](https://sectumsempra82.github.io/auto-subtitle-plus/) · [Mac installation and upgrades](https://sectumsempra82.github.io/auto-subtitle-plus/guide/#mac-updates) · [Transcription guide](https://sectumsempra82.github.io/auto-subtitle-plus/guide/#transcription)

## Changes

- Visible **Text transcript (TXT) settings** shortcut above the desktop settings tabs. It opens Files and focuses the existing export control without changing output settings.
- Renamed **Text file** to **Transcript (TXT)**, with guidance for TXT only, subtitles only, or both. The empty queue and Speech/Translate/Files help now explain transcription.
- TXT-only CLI example and clearer `--output-txt` help. TXT remains plain text without timestamps or speaker labels; review it as an editable draft.
- Transcription highlighted as the second use case in the README and website, with a dedicated desktop/CLI walkthrough in the integrated user guide.
- Native PKG packaging with a fixed Applications destination, stable package identity, whole-bundle replacement, version checking and a GUI close-app requirement. The website prefers PKG downloads when a release offers them and retains ZIP compatibility.
- Increasing Mac build numbers: this release uses **build 5**; build 4 was a local installer prototype. The Python package version is **0.3.0rc4** and the app's marketing version is **0.3.0**.
- Built-in desktop hardware guidance and contextual speech-setting help from the current main branch, including memory/compute tradeoffs and consistency guidance.

Speech recognition, translation, export calculations, persisted settings keys and subtitle defaults are unchanged. Transcription does not add speaker identification, summarization or a built-in editor.

## Validation and limits

- Full Python regression suite: **221 tests, 2 platform-specific skips**.
- Native Cocoa desktop suite: **6 passed**, including launcher behavior, tab layout and live appearance changes.
- New native packaging integration test builds and extracts a signed dummy app, checking app-only payload scope, replacement/version/identity policies, platform requirements, close-app declaration and checksum.
- Final PKG/ZIP checksums and app signature verified. Native Installer choice evaluation accepts the package. The extracted app launches with isolated state and a minimal environment.
- Website release selection tests cover PKG preference, ZIP fallback, independent Windows/Mac releases, invalid URLs, drafts and unavailable GitHub API. Desktop and mobile transcription layouts and guide anchors were checked.

This build was validated on **macOS 27.0 Apple Silicon**. It is not a clean-machine certification. **An actual administrator-authorized installation, ZIP-to-PKG overwrite, downgrade attempt and live close-app prompt have not been exercised.** Replacement and version protection are configured and verified in generated Installer metadata, not claimed as observed end-to-end installation results.

Intel Macs, pre-26 macOS, Apple GPU acceleration and every model/output combination remain unsupported or unvalidated. Mac translation remains local-only; CUDA and Windows-only Hy-MT2 are unavailable. Recognition and translation quality were not re-benchmarked for these UI/packaging changes.

## Source and notices

The app includes third-party notices and its dependency inventory. `AutoSubtitlePlus-macOS-NativeSources.tar` and its checksum provide the pinned matching Qt/PySide, PyAV/FFmpeg codec sources and upstream build scripts. PyAV includes GPL codecs; the project's source remains MIT licensed, and the combined GPL-covered binary distribution is supplied under GPLv3 terms. Preserve applicable notices and matching sources when redistributing.

[Tagged application source](https://github.com/Sectumsempra82/auto-subtitle-plus/tree/v0.3.0-rc.4) · [Mac build instructions](https://github.com/Sectumsempra82/auto-subtitle-plus/blob/v0.3.0-rc.4/packaging/macos.md)

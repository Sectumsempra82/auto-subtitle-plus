# v0.3.0-rc.9 — Redesigned macOS desktop

This release gives the Apple Silicon desktop a new Mac-only workspace inspired by the four-screen design reference. Queue, Speech, Translate, processing/output review, Settings, and Help now have dedicated places and clearer controls. The Windows desktop keeps its existing interface; the CLI processing pipeline is unchanged.

## New Mac workspace

- **Queue:** a persistent file table, add-file and folder actions, drag-and-drop area, start/pause/cancel controls, queue settings, and live CPU, memory, GPU, network, and disk meters.
- **Speech:** focused controls for backend, model, language, device, batch size, VAD, word timestamps, and consistency, with inline explanations.
- **Translate:** target language, provider, route, model, device, bilingual output, context, glossary JSON, and route preview in one form. Local translation remains the default. The Google provider is also available and requires a network connection; Offline mode, context, and glossary fields cannot be combined with it. CUDA and the Windows-only Hy-MT2 runtime remain unavailable on Mac.
- **History and output review:** current or completed file status, elapsed and estimated remaining time, stage progress, saved transcript and translation previews, subtitle cue table, output files, and activity. The previews are read-only; generated files can be opened through the existing controls.
- **Settings:** Layout, Files, and System retain the existing GUI/CLI output and processing choices. Queue state, settings, models, and exports remain outside the app bundle and survive a manual upgrade.

The visible screenshots in the README and guide were captured from the actual native Mac window with illustrative example filenames and saved output text. They are UI examples, not a transcription quality benchmark.

## Install and update

The Apple Silicon PKG installs or replaces `/Applications/Auto Subtitle Plus.app`; the ZIP remains available for manual drag-and-Replace installation. Finish jobs and quit the app before updating. FFmpeg/ffprobe are separate prerequisites. The installer is unsigned and the app is ad-hoc signed, not Developer ID signed or notarized. The bundle requires macOS 26+ and Apple Silicon; this release was validated on the maintainer's macOS 27.0 machine, not every supported configuration.

The package version is `0.3.0rc9`, Git tag `v0.3.0-rc.9`, and Mac bundle/installer build number `8`. The release includes PKG and ZIP SHA-256 files and the matching native-source archive. Windows binaries, if attached by the tagged workflow, use the existing Windows interface.

See the [Mac setup and usage guide](https://sectumsempra82.github.io/auto-subtitle-plus/guide/#macos) and [Mac packaging notes](https://github.com/Sectumsempra82/auto-subtitle-plus/blob/v0.3.0-rc.9/packaging/macos.md) for requirements, manual updates, and validation details.

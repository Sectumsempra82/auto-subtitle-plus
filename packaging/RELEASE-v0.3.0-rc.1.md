# Auto Subtitle Plus 0.3.0-rc.1

First Windows release candidate with a shared processing library, CLI and local-file desktop GUI.

## Download And Run

Choose the CLI or GUI Windows x64 ZIP below, extract the entire folder into a
writable location, then launch its EXE. No existing Python installation is needed.
Each ZIP is under 2 MiB, including README screenshots. SHA256 checksum files are attached.

First run downloads pinned, checksum-verified dependencies from official public
sources into app-local `data`. The CLI CPU archives total approximately 472 MiB;
prepared CPU runtime files occupy approximately 1.91 GiB, plus retained archives.
GUI and optional CUDA dependencies add to those totals. Models are downloaded
separately when selected and are not included in the release.

CPU is the initial runtime profile. `Setup CUDA.cmd` explicitly prepares the
larger GPU runtime; compatible NVIDIA drivers must already be installed. There
are no automatic system-wide installs, global PATH/Python changes or runtime
pip/compiler requirements. Any unavoidable system installer requires separate
explicit approval. `Check Dependencies.cmd` and `Repair Dependencies.cmd` provide
integrity checking and recovery. Preserve `data` when upgrading; both editions
can share `AUTO_SUBTITLE_PLUS_DATA_DIR`.

## Included

- Shared transcription, translation, subtitle layout/export and resource services.
- Compact GUI with local-file queue, exposed settings, progress, cancellation,
  retry, persistent queue/settings and CPU/RAM/GPU/network/disk telemetry.
- Original-language transcription, optional local translation, explicit English
  pivot, separate original/intermediate exports and opt-in bilingual subtitles.
- Whisper/Stable-TS and Faster-Whisper backends; curated local translation catalog
  with automatic model acquisition and offline reuse.
- Small native launchers, app-local dependency management, reviewed upstream
  credits, pinned download catalog and repeatable `Build Portable.cmd` workflow.

## Validation And Limitations

- 211 regression tests passed before publication.
- Fresh CPU dependency acquisition, installed-file integrity checks, cached
  offline CLI startup, real video transcription and OPUS-MT French SRT/TXT export
  passed on the development machine.
- Native GUI window and completed file-queue smoke tests passed.
- Clean-Windows VM validation and the new CUDA-bootstrap acquisition path are
  still pending. Earlier GPU model/full-bundle checks do not certify this package.
- These executables are unsigned. Windows SmartScreen may warn; do not disable
  antivirus or system-wide protection. Windows 10/11 x64 with inbox Windows
  PowerShell/.NET Framework is required. Model-specific license restrictions apply.

This is an explicitly marked pre-release, not a stable/certified installer.
See README.md and packaging/README.md in the tagged source for full commands,
build instructions, dependency provenance, licenses and all upstream/fork credits.

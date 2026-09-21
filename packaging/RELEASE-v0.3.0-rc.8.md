# v0.3.0-rc.8 — Full Whisper language coverage

This release enables the complete 100-language Whisper registry for speech
recognition and keeps subtitle, transcript, cache, and metadata files UTF-8
safe across SRT, VTT, TXT, and JSON outputs.

Translation remains separately validated by model and language direction. The
release does not claim translation quality parity for every Whisper language.

Model-quality benchmarking is intentionally not part of this milestone.

## What changed

### Whisper language coverage

The canonical language registry in `auto_subtitle_plus/model_manager.py` now
contains the complete 100-language list used by Whisper. The desktop Speech
language selector is generated from that registry, so CLI and GUI language
choices cannot silently drift apart.

The registry includes Romanian (`ro`) and lower-resource languages in addition
to the major population and film-production languages. Whisper aliases such as
`mandarin`, `moldovan`, `panjabi`, `sinhalese`, `burmese`, and `javanese` are
normalized to their canonical codes.

ASR language support is intentionally independent from translation support. A
language can be selected for transcription even when no local translation
model has a validated route for it. Translation model selection continues to
reject unsupported source/target directions.

### Encoding and file safety

The milestone focuses on preserving recognized and translated text at the file
boundary rather than measuring model quality. Text is kept as Unicode through
the pipeline and written as UTF-8 for:

- SRT subtitles
- VTT subtitles
- plain-text transcripts
- JSON stage/cache metadata
- bilingual subtitles
- filenames containing non-ASCII characters

Published files continue to use atomic replacement, so a failed write does not
replace a previously completed output with a partial file. JSON output keeps
Unicode characters readable with `ensure_ascii=False`; input paths and cache
paths remain separate from the encoded file content.

### Regression coverage

The milestone added contract coverage for:

- the complete 100-code registry and language aliases;
- Romanian and Mandarin normalization;
- Unicode text containing Latin accents, Cyrillic, Arabic, CJK, Japanese,
  Turkish characters, em dashes, and emoji-adjacent text;
- SRT, VTT, TXT, bilingual, atomic-write, and non-ASCII filename behavior;
- desktop selector generation from the shared registry.

The changed-area verification completed with 71 passing tests. Full-suite
macOS runs may still require a less restricted process environment because
some existing tests inspect process trees with `psutil` and compare temporary
directory paths across `/var` and `/private/var`.

## Build and release

The rc8 package version is `0.3.0rc8`, with Git tag `v0.3.0-rc.8`.

On Apple Silicon, the release build uses:

```sh
.venv/bin/python tools/package_macos.py --build-number 7
```

This produces the app bundle, PKG installer, ZIP archive, and SHA-256 files
under `dist/`. Windows x64 CLI and GUI artifacts are produced by the tagged
GitHub Actions workflow; Windows was not falsely represented as locally built
on macOS.

For local macOS development, `/Applications/Auto Subtitle Plus.app` points to
the checkout build at `dist/macos-release/Auto Subtitle Plus.app`. The prior
installed bundle was preserved as `Auto Subtitle Plus.app.rc7-backup` for
rollback. This symlink is a developer convenience, not part of the distributed
PKG or ZIP.

## Relevant implementation files

- `auto_subtitle_plus/model_manager.py` — canonical language registry and aliases
- `auto_subtitle_plus/desktop/settings.py` — shared desktop language selector
- `auto_subtitle_plus/translation_pipeline.py` — open-language normalization and UTF-8 atomic writes
- `tests/test_model_manager_contract.py` — language registry contracts
- `tests/test_translation_pipeline_contract.py` — Unicode and output-format contracts
- `packaging/macos.md` — Apple Silicon build and installation documentation

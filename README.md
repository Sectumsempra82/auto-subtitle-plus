# Auto Subtitle Plus

Auto Subtitle Plus generates subtitles for video or audio files, can optionally translate them, and can embed subtitles back into video outputs.

This fork keeps original-language subtitles as the default. Translation only happens when `--translate-to` is provided, and bilingual output only happens when `--bilingual` is provided.

---

## Credits And Sources

This project is built on top of work from the auto-subtitle fork network. Features and fixes were reviewed and selectively ported from these repositories:

- [m1guelpf/auto-subtitle](https://github.com/m1guelpf/auto-subtitle): original project and core Whisper/FFmpeg workflow.
- [Sectumsempra82/auto-subtitle-plus](https://github.com/Sectumsempra82/auto-subtitle-plus): this maintained fork, package naming, installer/dependency fixes, language controls, wildcard handling, audio extraction improvements, and parallel extraction workflow.
- [seyithokelek/auto-subtitle-plus](https://github.com/seyithokelek/auto-subtitle-plus): enhanced local/fork work used as the main feature source for Stable Whisper integration, optional translation, grouped CLI, original-language default behavior, `.ogg` audio support, and explicit `--bilingual` handling.
- [zaltinsoy/AutoSubZ](https://github.com/zaltinsoy/AutoSubZ): ideas ported for VTT output, TXT transcript output, MKV soft subtitles, word timestamp option, and related CLI behavior.
- [Irvingouj/auto-subtitle](https://github.com/Irvingouj/auto-subtitle): FFmpeg progress/error reporting approach adapted for extraction and embedding steps.
- RapDoodle contributions already present in the project history: language option, wildcard support, parallel audio extraction, and audio sync fixes.

Other forks in the network were reviewed, but not all features were merged. Heavy GUI, Llama/transformers translation, lockfile/tooling migrations, sample media, and rename-only changes were intentionally left out.

---

## What's New

- Original-language subtitles by default.
- Optional translation with `--translate-to`.
- Optional bilingual subtitles with `--bilingual`.
- SRT or VTT subtitle output with `--subtitle-format`.
- Plain text transcript output with `--output-txt`.
- Soft-subtitle MKV output with `--output-mkv`.
- Stable Whisper integration via `stable-ts`.
- Optional word timestamp request with `--word-timestamps`.
- Audio file inputs, including `.mp3`, `.ogg`, `.wav`, `.flac`, `.m4a`, `.wma`, and `.aac`.
- Batch translation and parallel audio extraction controls.
- FFmpeg progress and clearer FFmpeg error reporting.

---

## Installation

Install from GitHub:

```bash
pip install git+https://github.com/Sectumsempra82/auto-subtitle-plus
```

For local development, install editable from this checkout:

```bash
pip install -e .
```

You also need the FFmpeg binary installed and available on your terminal PATH:

```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS with Homebrew
brew install ffmpeg

# Windows with Chocolatey
choco install ffmpeg
```

---

## Quick Usage

Generate an SRT subtitle file in the current directory:

```bash
auto_subtitle_plus video.mp4
```

Generate and embed subtitles into an MP4:

```bash
auto_subtitle_plus video.mp4 --output-video
```

Translate subtitles to French:

```bash
auto_subtitle_plus video.mp4 --translate-to fr --output-srt
```

Generate bilingual subtitles:

```bash
auto_subtitle_plus video.mp4 --translate-to fr --bilingual --output-srt
```

Generate VTT subtitles:

```bash
auto_subtitle_plus video.mp4 --subtitle-format vtt --output-srt
```

Save a plain text transcript too:

```bash
auto_subtitle_plus video.mp4 --output-srt --output-txt
```

Create an MKV with soft subtitles:

```bash
auto_subtitle_plus video.mp4 --output-video --output-mkv
```

Transcribe audio files directly:

```bash
auto_subtitle_plus audio.mp3 audio.ogg --output-srt
```

---

## Batch Examples

Process all MP4 files in the current directory:

```bash
auto_subtitle_plus *.mp4 --output-srt
```

Process multiple formats:

```bash
auto_subtitle_plus *.mp4 *.mkv *.mov --output-srt
```

Embed subtitles for multiple videos:

```bash
auto_subtitle_plus *.mp4 --output-video
```

Translate all videos to Turkish:

```bash
auto_subtitle_plus *.mp4 --translate-to tr --output-srt
```

PowerShell recursive processing:

```powershell
Get-ChildItem -Recurse -Include *.mp4,*.mkv,*.mov | ForEach-Object {
  auto_subtitle_plus $_.FullName --output-srt
}
```

Bash recursive processing:

```bash
shopt -s globstar nullglob
auto_subtitle_plus **/*.mp4 --output-srt
```

---

## Full Command List

```text
usage: auto_subtitle_plus [-h]
                          [-m {tiny.en,tiny,base.en,base,small.en,small,medium.en,medium,large-v1,large-v2,large-v3,large,large-v3-turbo,turbo}]
                          [-o OUTPUT_DIR] [-s] [-a] [-v]
                          [--subtitle-format {srt,vtt}] [--output-txt]
                          [--output-mkv] [--language LANGUAGE]
                          [--translate-off] [--translate-to TRANSLATE_TO]
                          [--bilingual] [--batch-size BATCH_SIZE]
                          [--max-workers MAX_WORKERS]
                          [--extract-workers EXTRACT_WORKERS]
                          [--device DEVICE] [--verbose]
                          [--enhance-consistency] [--word-timestamps]
                          paths [paths ...]
```

### Positional Arguments

```text
paths
  Input file paths or wildcards, for example `video.mp4`, `*.mp4`, or multiple paths.
```

### General Options

```text
-h, --help
  Show help and exit.

-m, --model
  Whisper model to use.
  Choices: tiny.en, tiny, base.en, base, small.en, small, medium.en, medium,
  large-v1, large-v2, large-v3, large, large-v3-turbo, turbo.
  Default: small.

-o, --output-dir
  Directory to save outputs.
  Default: current directory.
```

### Output Options

```text
-s, --output-srt
  Generate a subtitle file.

-a, --output-audio
  Save extracted audio.

-v, --output-video
  Generate video with embedded subtitles.

--subtitle-format {srt,vtt}
  Subtitle file format.
  Default: srt.

--output-txt
  Also save a plain text transcript.

--output-mkv
  When outputting video, mux subtitles as a soft track in an MKV container.
```

### Language Options

```text
--language LANGUAGE
  Force audio language, for example `en`, `fr`, `tr`, `es`.

--translate-off
  Deprecated compatibility flag. Original-language subtitles are now the default.

--translate-to TRANSLATE_TO
  Target language for translation, for example `tr`, `fr`, `es`.

--bilingual
  When translating, include original text above translated text.
```

### Performance Options

```text
--batch-size BATCH_SIZE
  Segments per translation batch.
  Default: 10.

--max-workers MAX_WORKERS
  Max parallel translation threads.
  Default: 4.

--extract-workers EXTRACT_WORKERS
  Audio extraction workers.
  Default: half of CPU cores.
```

### Advanced Options

```text
--device DEVICE
  Processing device.
  Default: cuda when available, otherwise cpu.

--verbose
  Show detailed processing logs.

--enhance-consistency
  Improve transcription consistency by conditioning on previous text.

--word-timestamps
  Ask Stable Whisper to include word-level timestamps.
```

---

## CPU/GPU Examples

Force CPU:

```bash
auto_subtitle_plus video.mp4 --device cpu
```

Use CUDA:

```bash
auto_subtitle_plus video.mp4 --device cuda
```

Use a larger model:

```bash
auto_subtitle_plus video.mp4 --model large --device cuda --output-srt
```

Tune extraction and translation parallelism:

```bash
auto_subtitle_plus *.mp4 --output-srt --batch-size 20 --max-workers 8 --extract-workers 4
```

---

## License

MIT. See [LICENSE](LICENSE).

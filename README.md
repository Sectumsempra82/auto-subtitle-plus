# Auto Subtitle Plus (Enhanced Fork)

This project is a **clean and improved fork** of the original auto-subtitle tool.
Goal: simplify usage, improve stability, and add optional translation/output formats.

<p style="color:blue;">

Bu ara?, videolar?n?z i?in otomatik altyaz? olu?turabilir, altyaz?lar? istedi?iniz dile ?evirebilir
veya hem orijinal hem de ?evrilmi? altyaz?y? (?ift dil; ?rne?in t?rk?e-ingilizce altyaz?) ayn? anda ?retebilir.
</p>


---

## What's New in This Version
- **Optional subtitle translation** with `--translate-to tr|fr|...` (Google Translate backend)
- Original-language subtitles by default
- Bilingual subtitles available with `--bilingual`
- SRT or VTT subtitle output with `--subtitle-format`
- Plain text transcript output with `--output-txt`
- Soft-subtitle MKV output with `--output-mkv`
- Optional stable-whisper word timestamps with `--word-timestamps`
- Batch translation & **multithreading** for speed
- Clear, grouped CLI (Output, Language, Performance, Advanced)
- **Stable Whisper** integration for robust transcription
- Subtitled videos saved with `_subtitled.mp4`
- Cleaner logs and small performance tweaks

---

## Installation
Supports Python **3.7 ? 3.11**.

<div style="color:blue; padding: 8px 10px; border-left: 4px solid #1f6feb; background:#f0f8ff;">
<strong>Quick install (from GitHub):</strong><br/>
<pre style="margin: 6px 0 0;"><code>pip install git+https://github.com/Sectumsempra82/auto-subtitle-plus</code></pre>
</div>

Also install <strong>ffmpeg</strong> (if not already installed):

```bash
# Ubuntu/Debian
sudo apt install ffmpeg
# macOS (Homebrew)
brew install ffmpeg
# Windows (Chocolatey)
choco install ffmpeg
```

---

## Quick Usage

### Generate subtitles only (default)
By default, this creates an `.srt` next to your video (it **does not** embed unless you ask for it):
```bash
auto_subtitle_plus video_name.*
```
This produces `video_name.srt`.

### Generate and embed subtitles into video
```bash
auto_subtitle_plus video.mp4 --output-video
```
Outputs `video_subtitled.mp4`.

### Translate subtitles to another language
```bash
auto_subtitle_plus video.mp4 --translate-to fr --output-srt
```

### Generate bilingual subtitles
```bash
auto_subtitle_plus video.mp4 --translate-to fr --bilingual --output-srt
```

### Keep original language
```bash
auto_subtitle_plus video.mp4 --output-srt
```

### Generate VTT subtitles
```bash
auto_subtitle_plus video.mp4 --subtitle-format vtt --output-srt
```

### Save a plain text transcript too
```bash
auto_subtitle_plus video.mp4 --output-srt --output-txt
```

### Create an MKV with soft subtitles
```bash
auto_subtitle_plus video.mp4 --output-video --output-mkv
```

---

## Batch Processing Examples

### Process all MP4 files in the current directory (SRT only)
```bash
auto_subtitle_plus *.mp4 --output-srt
```

### Process multiple formats in one go
```bash
auto_subtitle_plus *.mp4 *.mkv *.mov --output-srt
```

### Process audio files directly
```bash
auto_subtitle_plus *.mp3 *.ogg --output-srt
```
Audio inputs are transcribed directly. Video embedding is skipped for audio-only files.

### Embed subtitles for multiple files
```bash
auto_subtitle_plus *.mp4 --output-video
```
Creates `*_subtitled.mp4` for each input that produced subtitles.

### Translate all videos to Turkish and save SRT
```bash
auto_subtitle_plus *.mp4 --translate-to tr --output-srt
```

### (Linux/macOS) Recurse into subfolders (Bash)
```bash
shopt -s globstar nullglob
auto_subtitle_plus **/*.mp4 --output-srt
```

### (Windows PowerShell) Recurse into subfolders
```powershell
Get-ChildItem -Recurse -Include *.mp4,*.mkv,*.mov | ForEach-Object {
  auto_subtitle_plus $_.FullName --output-srt
}
```

---

## CPU/GPU & Performance

### Force CPU
```bash
auto_subtitle_plus video.mp4 --device cpu
```

### Use GPU (CUDA)
```bash
auto_subtitle_plus video.mp4 --device cuda
```
> Ensure your PyTorch installation has CUDA support. If CUDA is unavailable, the tool falls back to CPU.

### Tune parallelism and throughput
- **Audio extraction workers** (CPU processes): `--extract-workers 4`
- **Translation batch size**: `--batch-size 20`
- **Translation threads**: `--max-workers 8`

Example (batch SRT with tuned settings):
```bash
auto_subtitle_plus *.mp4 --output-srt --batch-size 20 --max-workers 8 --extract-workers 4
```

### Larger models on GPU
```bash
auto_subtitle_plus *.mp4 --model large --device cuda --output-srt
```

---

## Show All Options
```bash
auto_subtitle_plus --help
```

---

## License
MIT. See [LICENSE](LICENSE).

> This fork acknowledges and builds upon the original work by Sectumsempra82 and its README (provided as README_org.md).

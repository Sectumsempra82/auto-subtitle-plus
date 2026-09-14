"""Built-in settings guidance shared by the Windows and macOS desktops."""

TAB_GUIDANCE: dict[str, str] = {
    "Speech": "Turn audio into text here. Backend is the engine; model is the trained speech recognizer. Hover over fields and labels for tradeoffs.",
    "Translate": "Translation runs after transcription and has its own model and device. Audio language belongs in Speech; the target language belongs here. Leave Translate off for original-language subtitles.",
    "Layout": "These are readability targets for translated captions. Start with 42 characters per line, 2 lines and 17 characters per second; review the result in a player. Bilingual output preserves source timing.",
    "Files": "Choose which outputs to keep. SRT is a good general-purpose subtitle file; MP4 burn makes captions permanent, while MKV soft keeps them selectable. Video export takes extra time and disk space.",
    "System": "First use may download models and runtimes. Prepare them before enabling Offline. Retry translation requires a matching cached transcript; turn it off to transcribe again. Cache storage is disk space, not GPU memory.",
}

HARDWARE_GUIDE = """<b>Memory and model choice</b><br>
VRAM is dedicated GPU memory. System RAM and model download size are different budgets.
Other apps use VRAM too; check free memory, not just the capacity printed on the GPU.<br><br>
<b>Published reference figures, not guaranteed requirements</b><br>
OpenAI Whisper lists roughly 1 GB for tiny/base, 2 GB for small, 5 GB for medium,
10 GB for large and 6 GB for turbo. These are a reference for the stable backend;
Stable-TS and workload overhead can differ.<br>
Faster-whisper's large-v2 benchmark uses about 4.5 GB with float16 or 2.9 GB with INT8
without batching; batch 8 raises these to about 6.1 GB and 4.5 GB.
Large-v3 is similarly sized: treat those as planning estimates, not a large-v3 measurement.<br><br>
<b>Practical NVIDIA starting points</b><br>
4 GB: start with small, faster, int8_float16, batch 1.<br>
6 GB: start with medium; large-v3 with int8_float16 may fit if enough VRAM is free.<br>
8 GB: large-v3, faster, CUDA, int8_float16, batch 1 is a reasonable starting point.<br>
12 GB or more: try float16; increase batching gradually while watching memory.<br>
These are suggestions, not automatic settings. GPU support and available memory still matter.<br><br>
<b>Compute type</b><br>
auto lets the engine choose; it is not a promise to fit available memory.
float16 uses half precision and is a good compatible-GPU option.
int8_float16 combines compressed 8-bit weights with half-precision computation for GPU headroom.
int8 is a useful CPU starting point and can also run on supported GPUs.
float32 uses more memory and is useful for CPU compatibility, not as a quality upgrade.
Quantization can change the transcript slightly; compare a short sample when accuracy matters.<br><br>
<b>If memory runs out</b><br>
Use batch 1, close other GPU-heavy apps, try int8_float16 with faster, or choose a smaller model.
CPU processing uses system RAM and is generally slower. Translation has a separate memory budget.
A missing CUDA runtime or unsupported compute type is a different problem from insufficient VRAM.<br><br>
<b>Quality and speed</b><br>
Tiny/base are useful for quick checks; larger models usually trade more time and memory for accuracy.
Turbo prioritizes speed. distil-large-v3.5 requires faster and English audio in this app.
Set the spoken language when known. Test a representative clip before a long queue.<br><br>
<b>Sources</b> (opens online):
<a href="https://github.com/openai/whisper#available-models-and-languages">OpenAI model table</a> ·
<a href="https://github.com/SYSTRAN/faster-whisper#benchmark">Faster-whisper benchmarks</a>.
This guide itself is available offline.
"""

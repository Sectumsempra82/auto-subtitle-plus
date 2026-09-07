from __future__ import annotations

import argparse
import contextlib
import glob
import json
import multiprocessing
import os
import sys
import threading
import warnings

import psutil

from . import processing as _processing
from .backends import (
    BACKENDS,
    asr_backend_fingerprint,
    format_backend_error,
    positive_int,
    print_backend_models,
    validate_backend_options,
)
from .processing import (
    arg_value,
    cache_source_transcript,
    close_asr_model,
    close_translation_runtime,
    create_subtitled_videos,
    default_cache_dir,
    default_device,
    ffmpeg_extract_audio,
    generate_source_transcripts,
    generate_subtitles,
    generate_subtitles_from_sources,
    is_audio,
    is_english,
    load_backend_model,
    output_cues_for_source,
    parse_glossary,
    print_translation_models,
    print_translation_progress,
    print_translation_selection,
    print_translation_warnings,
    read_cached_source,
    run_ffmpeg_with_progress,
    run_transcription_worker,
    source_cache_key,
    source_cues_from_transcript,
    source_worker_args,
    subtitle_layout_is_adaptive,
    validate_translation_options,
    write_source_exports,
    write_subtitle,
    write_subtitle_atomic,
    write_txt_atomic,
)
from .translation_pipeline import TranslationPipeline, clear_translation_cache

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

def extract_audio_worker(input_path, output_path):
    return _processing.extract_audio_worker(input_path, output_path)


def get_audio(paths, save_audio, output_dir, num_workers, context=None):
    return _processing.get_audio(paths, save_audio, output_dir, num_workers, context)


def source_stage_settings(args):
    return _processing.source_stage_settings(args)


def cacheable_source_stage_settings(args):
    return _processing.cacheable_source_stage_settings(args)


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.clear_translation_cache:
        clear_translation_cache(args.translation_cache_dir)
        print(f"Translation cache cleared: {os.path.abspath(args.translation_cache_dir or default_cache_dir())}")
        return 0

    if args.list_translation_models:
        try:
            print_translation_models(args)
        except Exception as error:
            print(f"Translation model listing failed: {error}")
            return 1
        return 0

    if args.list_models:
        try:
            print_backend_models(args.backend)
        except Exception as error:
            print(format_backend_error(error))
            return 1
        return 0

    try:
        validate_backend_options(args, parser)
        validate_translation_options(args, parser)
        args.glossary = parse_glossary(args.glossary)
    except ValueError as error:
        parser.error(str(error))

    json_stream = sys.stdout
    human_print = stderr_print if args.progress_json else print
    resource_stream = json_stream if args.progress_json else sys.stderr
    monitor = ResourceSummaryPrinter(args.resources, args.output_dir, stream=resource_stream)
    monitor.start()
    try:
        if args.progress_json:
            with contextlib.redirect_stdout(sys.stderr):
                result = _processing.run_job(args, progress=cli_progress(True, json_stream), stdout=human_print)
        else:
            result = _processing.run_job(args, progress=None, stdout=human_print)
    finally:
        monitor.stop()

    if result.error:
        human_print(result.error)
    return 0 if result.status == "completed" else 1


def build_parser():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Auto Subtitle Plus - Automatically generate and translate subtitles for video/audio files",
        epilog="""Examples:
  Basic usage:
    auto_subtitle_plus video.mp4 --output-video

  Force English transcription:
    auto_subtitle_plus video.mp4 --language en

  Translate to French:
    auto_subtitle_plus video.mp4 --translate-to fr --output-srt

  Create bilingual subtitles:
    auto_subtitle_plus video.mp4 --translate-to fr --bilingual --output-srt

  Transcribe audio directly:
    auto_subtitle_plus audio.mp3 audio.ogg --output-srt

  Use large model with GPU:
    auto_subtitle_plus video.mp4 --model large --device cuda""",
    )

    parser.add_argument("paths", nargs="*", help="Input file paths or wildcards (e.g., *.mp4)")
    parser.add_argument("--backend", default="stable", choices=BACKENDS, help="Transcription backend (default: %(default)s)")
    parser.add_argument("--list-models", action="store_true", help="List models for the selected backend and exit without loading a model")
    parser.add_argument("--list-translation-models", action="store_true", help="List local translation models for the selected translation route and exit")
    parser.add_argument("-m", "--model", default="small", help="whisper model to use (default: %(default)s)")
    parser.add_argument("-o", "--output-dir", default=os.getcwd(), help="Output directory (default: current directory)")

    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument("-s", "--output-srt", action="store_true", help="Generate SRT subtitle file")
    output_group.add_argument("-a", "--output-audio", action="store_true", help="Save extracted audio file")
    output_group.add_argument("-v", "--output-video", action="store_true", help="Generate video with embedded subtitles")
    output_group.add_argument("--subtitle-format", choices=("srt", "vtt"), default="srt", help="Subtitle file format (default: %(default)s)")
    output_group.add_argument("--output-txt", action="store_true", help="Also save final text (translated when --translate-to is used)")
    output_group.add_argument("--output-mkv", action="store_true", help="When outputting video, mux subtitles as a soft track in an MKV container")
    output_group.add_argument("--no-overwrite", dest="overwrite", action="store_false", default=True, help="Fail when an output file already exists")

    lang_group = parser.add_argument_group("Language Options")
    lang_group.add_argument("--language", type=str, default=None, help="Force audio language (e.g., en, fr, tr)")
    lang_group.add_argument("--translate-off", action="store_true", help="Deprecated; original-language subtitles are now the default")
    lang_group.add_argument("--translate-to", default=None, help="Target language for translation (e.g., it, fr, es, de, pt)")
    lang_group.add_argument("--translation-backend", "--translation-engine", choices=("local", "google"), dest="translation_engine", default="local", help="Translation engine. Local is default; Google is explicit network translation")
    lang_group.add_argument("--translation-route", choices=("direct", "via-en"), default="direct", help="Translation route. via-en explicitly translates source -> English -> target")
    lang_group.add_argument("--translation-model", default=None, help="Local translation model id or family alias, for example opus-mt")
    lang_group.add_argument("--translation-device", choices=("auto", "cpu", "cuda"), default="auto", help="Local translation device (default: %(default)s)")
    lang_group.add_argument("--translation-cache-dir", default=None, help="Translation cache directory (default: LocalAppData AutoSubtitlePlus cache)")
    lang_group.add_argument("--retry-translation", action="store_true", help="Reuse a cached source transcript and retry only translation/output stages")
    lang_group.add_argument("--clear-translation-cache", action="store_true", help="Clear cached source and translation stages, then exit")
    lang_group.add_argument("--offline", action="store_true", help="Forbid ASR and translation model downloads")
    lang_group.add_argument("--bilingual", action="store_true", help="When translating, include original text above translated text")
    lang_group.add_argument("--output-source-subtitles", "--save-original", dest="output_source_subtitles", action="store_true", help="When translating, also write name.source.<lang>.<format>")
    lang_group.add_argument("--output-intermediate-subtitles", "--save-intermediate", dest="output_intermediate_subtitles", action="store_true", help="When using via-en, also write name.intermediate.en.<format>")
    lang_group.add_argument("--subtitle-layout", choices=("adaptive", "preserve"), default="adaptive", help="Translated subtitle layout (default: %(default)s)")
    lang_group.add_argument("--no-adaptive-layout", action="store_true", help="Disable adaptive translated subtitle layout")
    lang_group.add_argument("--context", default="", help="Context passed to supported translation models")
    lang_group.add_argument("--glossary", default=None, help="JSON object or JSON file path with source term to target term mappings")

    layout_group = parser.add_argument_group("Layout Options")
    layout_group.add_argument("--max-chars-per-line", type=positive_int, default=42, help="Maximum subtitle characters per rendered line")
    layout_group.add_argument("--max-lines", type=positive_int, default=2, help="Maximum rendered subtitle lines")
    layout_group.add_argument("--max-cps", type=float, default=17.0, help="Maximum target characters per second")
    layout_group.add_argument("--min-duration", type=float, default=1.0, help="Minimum target subtitle duration")
    layout_group.add_argument("--max-duration", type=float, default=7.0, help="Maximum target subtitle duration")

    perf_group = parser.add_argument_group("Performance Options")
    perf_group.add_argument("--batch-size", type=int, default=10, help="Legacy compatibility option; local translation uses token-budget units")
    perf_group.add_argument("--max-workers", type=int, default=4, help="Legacy compatibility option; local translator jobs are sequential")
    perf_group.add_argument("--extract-workers", type=positive_int, default=max(1, (psutil.cpu_count(logical=False) or 2) // 2), help="Audio extraction workers (default: half of CPU cores)")

    adv_group = parser.add_argument_group("Advanced Options")
    adv_group.add_argument("--device", default=None, help="Processing device (default: cuda when available, otherwise cpu)")
    adv_group.add_argument("--compute-type", default="auto", help="faster-whisper compute type, such as auto, float16, int8, or int8_float16 (default: %(default)s)")
    adv_group.add_argument("--inference-batch-size", type=positive_int, default=1, help="faster-whisper inference batch size; values > 1 use BatchedInferencePipeline and require --vad (default: %(default)s)")
    adv_group.add_argument("--vad", action="store_true", help="Enable faster-whisper VAD filtering")
    adv_group.add_argument("--verbose", action="store_true", help="Show detailed processing logs")
    adv_group.add_argument("--enhance-consistency", action="store_true", help="Improve transcription consistency; unsupported with faster batched inference")
    adv_group.add_argument("--word-timestamps", action="store_true", help="Ask stable-whisper to include word-level timestamps")
    adv_group.add_argument("--progress-json", action="store_true", help="Emit machine-readable JSON progress events")
    adv_group.add_argument("--resources", action="store_true", help="Emit periodic JSON resource monitor snapshots while processing")
    return parser


def stderr_print(message):
    print(message, file=sys.stderr)


def cli_progress(enabled, stream=None):
    if not enabled:
        return None
    stream = stream or sys.stdout

    def emit(event):
        print(json.dumps(event, ensure_ascii=False, sort_keys=True), file=stream, flush=True)

    return emit


class ResourceSummaryPrinter:
    def __init__(self, enabled, disk_path, stream=None):
        self.enabled = enabled
        self.disk_path = disk_path
        self.stream = stream or sys.stderr
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        from .resources import ResourceMonitor

        monitor = ResourceMonitor(root_pid=os.getpid(), disk_path=self.disk_path)
        try:
            while not self._stop.wait(2.0):
                self._print_sample(monitor)
        finally:
            self._print_sample(monitor)
            monitor.close()

    def _print_sample(self, monitor):
        event = {"state": "resources", "sample": monitor.sample()}
        print(json.dumps(event, sort_keys=True), file=self.stream, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())

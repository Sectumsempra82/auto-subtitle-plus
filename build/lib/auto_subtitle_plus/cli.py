import os
import glob
import psutil
import ffmpeg
import argparse
import warnings
import tempfile
import multiprocessing
from io import StringIO
from .backends import (
    BACKENDS,
    asr_backend_fingerprint,
    format_backend_error,
    load_backend_model,
    print_backend_models,
    positive_int,
    validate_backend_options,
)
from .utils import (
    ffmpeg_extract_audio,
    get_filename,
    is_audio,
    run_ffmpeg_with_progress,
    write_subtitle,
    write_txt,
)
from .translation_pipeline import (
    PIPELINE_REVISION,
    TranslationPipeline,
    TranslationStageError,
    atomic_write_text,
    cache_source_transcript,
    clear_translation_cache,
    default_cache_dir,
    read_cached_source_transcript,
    source_as_subtitle_cues,
    source_cache_key,
    source_cues_from_transcript,
)
from .translation_types import TranslationRequest, TranslationSettings
from .transcription_worker import transcribe_source_worker

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description="Auto Subtitle Plus - Automatically generate and translate subtitles for video/audio files",
        epilog='''Examples:
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
    auto_subtitle_plus video.mp4 --model large --device cuda'''
    )

    # Core arguments
    parser.add_argument("paths", nargs="*", help="Input file paths or wildcards (e.g., *.mp4)")

    # Model and output options
    parser.add_argument("--backend",
                       default="stable",
                       choices=BACKENDS,
                       help="Transcription backend (default: %(default)s)")
    parser.add_argument("--list-models",
                       action="store_true",
                       help="List models for the selected backend and exit without loading a model")
    parser.add_argument("--list-translation-models",
                       action="store_true",
                       help="List local translation models for the selected translation route and exit")
    parser.add_argument("-m", "--model",
                       default="small",
                       help="whisper model to use (default: %(default)s)")

    parser.add_argument("-o", "--output-dir",
                       default=os.getcwd(),
                       help="Output directory (default: current directory)")

    # Output controls
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument("-s", "--output-srt",
                             action="store_true",
                             help="Generate SRT subtitle file")
    output_group.add_argument("-a", "--output-audio",
                             action="store_true",
                             help="Save extracted audio file")
    output_group.add_argument("-v", "--output-video",
                             action="store_true",
                             help="Generate video with embedded subtitles")
    output_group.add_argument("--subtitle-format",
                             choices=("srt", "vtt"),
                             default="srt",
                             help="Subtitle file format (default: %(default)s)")
    output_group.add_argument("--output-txt",
                             action="store_true",
                             help="Also save final text (translated when --translate-to is used)")
    output_group.add_argument("--output-mkv",
                             action="store_true",
                             help="When outputting video, mux subtitles as a soft track in an MKV container")

    # Language and translation
    lang_group = parser.add_argument_group('Language Options')
    lang_group.add_argument("--language",
                           type=str,
                           default=None,
                           help="Force audio language (e.g., en, fr, tr)")
    lang_group.add_argument("--translate-off",
                           action="store_true",
                           help="Deprecated; original-language subtitles are now the default")
    lang_group.add_argument("--translate-to",
                           default=None,
                           help="Target language for translation (e.g., it, fr, es, de, pt)")
    lang_group.add_argument("--translation-backend",
                           "--translation-engine",
                           choices=("local", "google"),
                           dest="translation_engine",
                           default="local",
                           help="Translation engine. Local is default; Google is explicit network translation")
    lang_group.add_argument("--translation-route",
                           choices=("direct", "via-en"),
                           default="direct",
                           help="Translation route. via-en explicitly translates source -> English -> target")
    lang_group.add_argument("--translation-model",
                           default=None,
                           help="Local translation model id or family alias, for example opus-mt")
    lang_group.add_argument("--translation-device",
                           choices=("auto", "cpu", "cuda"),
                           default="auto",
                           help="Local translation device (default: %(default)s)")
    lang_group.add_argument("--translation-cache-dir",
                           default=None,
                           help="Translation cache directory (default: LocalAppData AutoSubtitlePlus cache)")
    lang_group.add_argument("--retry-translation",
                           action="store_true",
                           help="Reuse a cached source transcript and retry only translation/output stages")
    lang_group.add_argument("--clear-translation-cache",
                           action="store_true",
                           help="Clear cached source and translation stages, then exit")
    lang_group.add_argument("--offline",
                           action="store_true",
                           help="Forbid ASR and translation model downloads")
    lang_group.add_argument("--bilingual",
                           action="store_true",
                           help="When translating, include original text above translated text")
    lang_group.add_argument("--output-source-subtitles",
                           "--save-original",
                           dest="output_source_subtitles",
                           action="store_true",
                           help="When translating, also write name.source.<lang>.<format>")
    lang_group.add_argument("--output-intermediate-subtitles",
                           "--save-intermediate",
                           dest="output_intermediate_subtitles",
                           action="store_true",
                           help="When using via-en, also write name.intermediate.en.<format>")
    lang_group.add_argument("--subtitle-layout",
                           choices=("adaptive", "preserve"),
                           default="adaptive",
                           help="Translated subtitle layout (default: %(default)s)")
    lang_group.add_argument("--no-adaptive-layout",
                           action="store_true",
                           help="Disable adaptive translated subtitle layout")

    # Performance settings
    perf_group = parser.add_argument_group('Performance Options')
    perf_group.add_argument("--batch-size",
                           type=int,
                           default=10,
                           help="Legacy compatibility option; local translation uses token-budget units")
    perf_group.add_argument("--max-workers",
                           type=int,
                           default=4,
                           help="Legacy compatibility option; local translator jobs are sequential")
    perf_group.add_argument("--extract-workers",
                           type=positive_int,
                           default=max(1, psutil.cpu_count(logical=False)//2),
                           help="Audio extraction workers (default: half of CPU cores)")

    # Advanced options
    adv_group = parser.add_argument_group('Advanced Options')
    adv_group.add_argument("--device",
                          default=None,
                          help="Processing device (default: cuda when available, otherwise cpu)")
    adv_group.add_argument("--compute-type",
                          default="auto",
                          help="faster-whisper compute type, such as auto, float16, int8, or int8_float16 (default: %(default)s)")
    adv_group.add_argument("--inference-batch-size",
                          type=positive_int,
                          default=1,
                          help="faster-whisper inference batch size; values > 1 use BatchedInferencePipeline and require --vad (default: %(default)s)")
    adv_group.add_argument("--vad",
                          action="store_true",
                          help="Enable faster-whisper VAD filtering")
    adv_group.add_argument("--verbose",
                          action="store_true",
                          help="Show detailed processing logs")
    adv_group.add_argument("--enhance-consistency",
                          action="store_true",
                          help="Improve transcription consistency; unsupported with faster batched inference")
    adv_group.add_argument("--word-timestamps",
                          action="store_true",
                          help="Ask stable-whisper to include word-level timestamps")

    args = parser.parse_args()
    if args.clear_translation_cache:
        clear_translation_cache(args.translation_cache_dir)
        print(f"Translation cache cleared: {os.path.abspath(args.translation_cache_dir or default_cache_dir())}")
        return 0

    if args.device is None:
        args.device = default_device()

    if args.list_translation_models:
        try:
            print_translation_models(args)
        except Exception as e:
            print(f"Translation model listing failed: {e}")
            return 1
        return 0

    if args.list_models:
        try:
            print_backend_models(args.backend)
        except Exception as e:
            print(format_backend_error(e))
            return 1
        return 0

    validate_backend_options(args, parser)
    validate_translation_options(args, parser)

    # Validate and resolve paths
    input_paths = []
    for pattern in args.paths:
        input_paths.extend(glob.glob(pattern))

    if not input_paths:
        print("Error: No valid input files found!")
        return 2

    if not args.output_video and not args.output_srt and not args.output_txt:
        args.output_srt = True

    # Handle .en models
    if args.model.endswith(".en"):
        args.language = "en"
        warnings.warn("Forcing English transcription")

    translation_requested = bool(args.translate_to or args.retry_translation)
    if translation_requested:
        sources, missing_paths = load_cached_sources(input_paths, args)
        failures = 0
        if args.retry_translation and missing_paths:
            for path in missing_paths:
                print(f"Cached source transcript not found for {path}; --retry-translation will not rerun ASR.")
            failures += len(missing_paths)
        elif missing_paths:
            audio_paths, failures = get_audio(
                missing_paths,
                args.output_audio,
                args.output_dir,
                args.extract_workers
            )
            worker_sources, source_failures = generate_source_transcripts(
                audio_paths,
                None,
                args
            )
            sources.update(worker_sources)
            failures += source_failures
    else:
        try:
            model = load_backend_model(args)
        except Exception as e:
            print(f"Model loading failed: {format_backend_error(e)}")
            return 1

        audio_paths, failures = get_audio(
            input_paths,
            args.output_audio,
            args.output_dir,
            args.extract_workers
        )
        sources, source_failures = generate_source_transcripts(
            audio_paths,
            model,
            args
        )
        failures += source_failures
        close_asr_model(model)
        model = None

    subtitles, subtitle_failures = generate_subtitles_from_sources(
        sources,
        args.output_srt,
        args.output_dir,
        args
    )
    failures += subtitle_failures

    if args.output_video:
        failures += create_subtitled_videos(
            input_paths,
            subtitles,
            args.output_dir,
            args.output_mkv
        )
    return 1 if failures else 0

def get_audio(paths, save_audio, output_dir, num_workers):
    successful_audio = {}
    tasks = []
    failures = 0

    for path in paths:
        if is_audio(path):
            successful_audio[path] = path
            continue

        target_dir = output_dir if save_audio else tempfile.gettempdir()
        output_path = os.path.join(target_dir, f"{get_filename(path)}.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        tasks.append((path, output_path))

    if tasks:
        with multiprocessing.Pool(num_workers) as pool:
            for input_path, output_path, error in pool.starmap(extract_audio_worker, tasks):
                if error:
                    print(f"Audio extraction failed for {input_path}: {error}")
                    failures += 1
                    continue
                successful_audio[input_path] = output_path

    audio_map = {
        path: successful_audio[path]
        for path in paths
        if path in successful_audio
    }
    return audio_map, failures

def extract_audio_worker(input_path, output_path):
    try:
        ffmpeg_extract_audio(input_path, output_path)
        return input_path, output_path, None
    except Exception as e:
        return input_path, output_path, str(e)

def generate_source_transcripts(audio_paths, model, args):
    sources = {}
    failures = 0

    for path, audio_path in audio_paths.items():
        print(f"\nProcessing: {os.path.basename(path)}")
        try:
            source_key = source_cache_key(path, source_stage_settings(args))
            cache_enabled = bool(arg_value(args, "translate_to", None) or arg_value(args, "retry_translation", False))
            if cache_enabled:
                cached = read_cached_source(path, args)
                if cached is not None:
                    sources[path] = cached
                    print("Using cached source transcript")
                    continue
            if model is None:
                worker_result = run_transcription_worker(path, audio_path, source_key, args)
                if not worker_result["ok"]:
                    print(f"Transcription failed: {worker_result['error']}")
                    failures += 1
                    continue
                language = worker_result["language"]
                cache_settings = cacheable_source_stage_settings(args)
                final_source_key = source_cache_key(path, cache_settings) if cache_settings is not None else source_key
                cues = source_cues_from_transcript(
                    {"language": language, "segments": worker_result["cues"]},
                    final_source_key,
                    language,
                )
                source_key = final_source_key
            else:
                result = model.transcribe(
                    audio_path,
                    language=args.language,
                    verbose=args.verbose,
                    condition_on_previous_text=args.enhance_consistency,
                    word_timestamps=args.word_timestamps,
                )
                cache_settings = cacheable_source_stage_settings(args)
                final_source_key = source_cache_key(path, cache_settings) if cache_settings is not None else source_key
                cues = source_cues_from_transcript(result, final_source_key, args.language)
                source_key = final_source_key
                language = cues[0].language if cues else "unknown"
            if cache_enabled and cache_settings is not None:
                cache_source_transcript(arg_value(args, "translation_cache_dir", None), source_key, cues, language)
            sources[path] = {"source_key": source_key, "language": language, "cues": cues}
        except Exception as e:
            print(f"Transcription failed: {format_backend_error(e)}")
            failures += 1
            continue

    return sources, failures


def load_cached_sources(input_paths, args):
    sources = {}
    missing = []
    for path in input_paths:
        cached = read_cached_source(path, args)
        if cached is None:
            missing.append(path)
        else:
            sources[path] = cached
            print(f"Using cached source transcript for: {os.path.basename(path)}")
    return sources, missing


def read_cached_source(path, args):
    cache_settings = cacheable_source_stage_settings(args)
    if cache_settings is None:
        return None
    source_key = source_cache_key(path, cache_settings)
    cached = read_cached_source_transcript(arg_value(args, "translation_cache_dir", None), source_key)
    if cached is None:
        return None
    language, cues = cached
    return {"source_key": source_key, "language": language, "cues": cues}


def run_transcription_worker(path, audio_path, source_key, args):
    context = multiprocessing.get_context("spawn")
    worker_args = source_worker_args(args)
    with context.Pool(1) as pool:
        return pool.apply(transcribe_source_worker, (path, audio_path, worker_args, source_key))


def source_worker_args(args):
    return {
        "backend": args.backend,
        "model": args.model,
        "language": args.language,
        "device": args.device,
        "compute_type": args.compute_type,
        "inference_batch_size": args.inference_batch_size,
        "vad": args.vad,
        "verbose": args.verbose,
        "enhance_consistency": args.enhance_consistency,
        "word_timestamps": args.word_timestamps,
        "offline": args.offline,
    }


def generate_subtitles(audio_paths, output_srt, output_dir, model, args):
    sources, source_failures = generate_source_transcripts(audio_paths, model, args)
    subtitles, subtitle_failures = generate_subtitles_from_sources(sources, output_srt, output_dir, args)
    return subtitles, source_failures + subtitle_failures


def generate_subtitles_from_sources(sources, output_srt, output_dir, args):
    subtitles = {}
    failures = 0
    pipeline = TranslationPipeline(arg_value(args, "translation_cache_dir", None))

    try:
        for path, source in sources.items():
            try:
                if arg_value(args, "output_source_subtitles", False) and args.translate_to:
                    write_source_exports(path, source, output_dir, args)
                final_cues, source_cues, intermediate_cues, raw_final_cues, raw_intermediate_cues, translation_warnings = output_cues_for_source(source, args, pipeline)
                print_translation_warnings(path, translation_warnings)
                subtitle_filename = f"{get_filename(path)}.{args.subtitle_format}"
                subtitle_dir = output_dir if output_srt else tempfile.gettempdir()
                subtitle_path = os.path.join(subtitle_dir, subtitle_filename)
                write_subtitle_atomic(
                    subtitle_path,
                    final_cues,
                    args.subtitle_format,
                    bilingual=args.bilingual and bool(args.translate_to),
                )
                subtitles[path] = subtitle_path
                if output_srt:
                    print(f"Subtitles saved to: {os.path.abspath(subtitle_path)}")

                if arg_value(args, "output_intermediate_subtitles", False) and intermediate_cues:
                    intermediate_path = os.path.join(
                        output_dir,
                        f"{get_filename(path)}.intermediate.en.{args.subtitle_format}",
                    )
                    write_subtitle_atomic(intermediate_path, intermediate_cues, args.subtitle_format, False)
                    print(f"Intermediate subtitles saved to: {os.path.abspath(intermediate_path)}")
                    if args.output_txt:
                        intermediate_txt_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.txt")
                        write_txt_atomic(intermediate_txt_path, raw_intermediate_cues or intermediate_cues)
                        print(f"Intermediate transcript saved to: {os.path.abspath(intermediate_txt_path)}")

                if args.output_txt:
                    txt_path = os.path.join(output_dir, f"{get_filename(path)}.txt")
                    write_txt_atomic(txt_path, raw_final_cues or final_cues)
                    print(f"Transcript saved to: {os.path.abspath(txt_path)}")
            except TranslationStageError as e:
                if arg_value(args, "output_intermediate_subtitles", False) and e.intermediate_cues:
                    intermediate_path = os.path.join(
                        output_dir,
                        f"{get_filename(path)}.intermediate.en.{args.subtitle_format}",
                    )
                    write_subtitle_atomic(intermediate_path, e.intermediate_cues, args.subtitle_format, False)
                    print(f"Intermediate subtitles saved to: {os.path.abspath(intermediate_path)}")
                    if args.output_txt:
                        intermediate_txt_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.txt")
                        write_txt_atomic(intermediate_txt_path, e.intermediate_cues)
                        print(f"Intermediate transcript saved to: {os.path.abspath(intermediate_txt_path)}")
                print(f"File write error: {str(e)}")
                failures += 1
            except Exception as e:
                print(f"File write error: {str(e)}")
                failures += 1
    finally:
        pipeline.close()
        close_translation_runtime()

    return subtitles, failures


def write_source_exports(path, source, output_dir, args):
    source_cues = source["cues"]
    source_path = os.path.join(
        output_dir,
        f"{get_filename(path)}.source.{source['language']}.{args.subtitle_format}",
    )
    write_subtitle_atomic(source_path, source_as_subtitle_cues(source_cues), args.subtitle_format, False)
    print(f"Source subtitles saved to: {os.path.abspath(source_path)}")
    if args.output_txt:
        source_txt_path = os.path.join(output_dir, f"{get_filename(path)}.source.{source['language']}.txt")
        write_txt_atomic(source_txt_path, source_cues)
        print(f"Source transcript saved to: {os.path.abspath(source_txt_path)}")


def output_cues_for_source(source, args, pipeline):
    source_cues = source["cues"]
    if not args.translate_to:
        return source_as_subtitle_cues(source_cues), source_cues, (), (), (), ()

    settings = TranslationSettings(
        source_language=source["language"],
        target_language=args.translate_to,
        route=arg_value(args, "translation_route", "direct"),
        engine=arg_value(args, "translation_engine", "local"),
        model_id=arg_value(args, "translation_model", None),
        device=arg_value(args, "translation_device", "auto"),
        offline=arg_value(args, "offline", False),
        bilingual=args.bilingual,
        adaptive_layout=subtitle_layout_is_adaptive(args),
        revision=PIPELINE_REVISION,
    )
    if settings.engine == "local":
        print_translation_selection(settings, source["language"], args.translate_to, args)
    result = pipeline.translate(
        TranslationRequest(
            source_key=source["source_key"],
            cues=source_cues,
            settings=settings,
            cache_dir=arg_value(args, "translation_cache_dir", None),
            progress=print_translation_progress,
        )
    )
    return result.final_cues, result.source_cues, result.intermediate_cues, result.raw_final_cues, result.raw_intermediate_cues, result.warnings


def print_translation_warnings(path, warnings):
    for warning in dict.fromkeys(warnings or ()):
        print(f"Translation warning for {os.path.basename(path)}: {warning}")


def write_subtitle_atomic(path, cues, subtitle_format, bilingual):
    buffer = StringIO()
    write_subtitle(
        cues,
        buffer,
        subtitle_format=subtitle_format,
        translate_off=True,
        bilingual=bilingual,
    )
    atomic_write_text(path, buffer.getvalue())


def write_txt_atomic(path, cues):
    buffer = StringIO()
    write_txt(cues, buffer)
    atomic_write_text(path, buffer.getvalue())


def source_stage_settings(args):
    return {
        "backend": args.backend,
        "model": args.model,
        "language": args.language,
        "device": args.device,
        "compute_type": args.compute_type if args.backend == "faster" else None,
        "inference_batch_size": args.inference_batch_size if args.backend == "faster" else None,
        "vad": args.vad if args.backend == "faster" else None,
        "word_timestamps": arg_value(args, "word_timestamps", False),
        "enhance_consistency": arg_value(args, "enhance_consistency", False),
        "asr_fingerprint": asr_backend_fingerprint(args),
        "revision": PIPELINE_REVISION,
    }


def cacheable_source_stage_settings(args):
    settings = source_stage_settings(args)
    if not settings["asr_fingerprint"].get("fingerprint_available"):
        return None
    return settings


def validate_translation_options(args, parser):
    if arg_value(args, "no_adaptive_layout", False):
        args.subtitle_layout = "preserve"
    if args.translate_off and args.translate_to:
        parser.error("--translate-off cannot be combined with --translate-to")
    if args.bilingual and not args.translate_to:
        parser.error("--bilingual requires --translate-to")
    if args.translation_route == "via-en" and not args.translate_to:
        parser.error("--translation-route via-en requires --translate-to")
    if args.translation_route == "via-en":
        if is_english(args.language):
            parser.error("--translation-route via-en requires a non-English source language")
        if is_english(args.translate_to):
            parser.error("--translation-route via-en requires a non-English target language")
    if args.translation_engine == "google" and args.offline:
        parser.error("--offline cannot be combined with --translation-engine google")
    if args.translate_to and args.translation_engine == "local":
        try:
            from .model_manager import normalize_language

            normalize_language(args.translate_to)
            if args.language:
                normalize_language(args.language)
        except Exception as e:
            parser.error(str(e))


def print_translation_models(args):
    from .model_manager import list_models, model_metadata

    if args.translation_route == "via-en" and args.translation_model == "opus-mt":
        print("Local translation models for explicit via-en route:")
        print("  opus-mt -> resolves each leg as opus-<source>-en + opus-en-<target>")
        return

    for spec in list_models(args.language, args.translate_to, route=args.translation_route):
        info = model_metadata(spec, args.translation_cache_dir)
        print(
            f"{spec.id}\t{spec.family}\t{info['status']}\t"
            f"{spec.source or '*'}->{spec.target or '*'}\t{spec.license}\t{spec.url}"
        )


def print_translation_selection(settings, source_language, target_language, args):
    from .model_manager import resolve_model, model_metadata

    if settings.route == "via-en":
        legs = ((source_language, "en"), ("en", target_language))
    else:
        legs = ((source_language, target_language),)
    for source, target in legs:
        spec = resolve_model(settings.model_id or "hy-mt2-1.8b-q8", source, target)
        info = model_metadata(spec, arg_value(args, "translation_cache_dir", None))
        mib = info["download_size"] / (1024 * 1024)
        notice = f" {info['notice']}" if info.get("notice") else ""
        print(
            f"Translation model selected: {spec.id} {source}->{target}, "
            f"revision {spec.revision[:12]}, {mib:.1f} MiB, {info['status']}, "
            f"license {spec.license}.{notice}"
        )


_last_download_progress = None


def print_translation_progress(event):
    global _last_download_progress
    state = event.get("state", "translation")
    message = event.get("message") or event.get("model") or event.get("file") or ""
    progress = event.get("progress")
    if progress is None and event.get("total_bytes", 0) > 0:
        progress = min(1.0, event.get("completed_bytes", 0) / event["total_bytes"])
    if state.startswith("downloading") and progress is not None:
        marker = (state, message, int(progress * 20))
        if marker == _last_download_progress:
            return
        _last_download_progress = marker
    else:
        _last_download_progress = None
    if progress is None:
        print(f"Translation {state}: {message}")
    else:
        print(f"Translation {state}: {progress:.0%} {message}")


def default_device():
    try:
        from torch.cuda import is_available
    except (ImportError, OSError):
        return "cpu"
    return "cuda" if is_available() else "cpu"


def close_asr_model(model):
    if model is None:
        return
    close = getattr(model, "close", None)
    if close is not None:
        close()


def close_translation_runtime():
    try:
        from .local_translation import close_translation_engines
    except ImportError:
        return
    close_translation_engines()


def is_english(language):
    return language is not None and language.strip().lower().replace("_", "-") in ("en", "english")


def arg_value(args, name, default):
    return getattr(args, name, default)


def subtitle_layout_is_adaptive(args):
    return arg_value(args, "subtitle_layout", "adaptive") == "adaptive" and not arg_value(args, "no_adaptive_layout", False)

def create_subtitled_videos(input_paths, subtitles, output_dir, output_mkv):
    failures = 0
    for path in input_paths:
        if is_audio(path) or path not in subtitles:
            continue

        subtitle_path = subtitles[path]
        output_ext = "mkv" if output_mkv else "mp4"
        output_filename = f"{get_filename(path)}_subtitled.{output_ext}"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)

        print(f"\nCreating subtitled video: {os.path.abspath(output_path)}")

        try:
            if output_mkv:
                command = ffmpeg.output(
                    ffmpeg.input(path),
                    ffmpeg.input(subtitle_path),
                    output_path,
                    vcodec="copy",
                    acodec="copy",
                    scodec="copy",
                ).compile(overwrite_output=True)
            else:
                video = ffmpeg.input(path)
                command = ffmpeg.output(
                    video.filter(
                        "subtitles",
                        filename=subtitle_path,
                        force_style="OutlineColour=&H40000000,BorderStyle=3",
                    ),
                    video.audio,
                    output_path,
                    vcodec="libx264",
                    acodec="copy",
                ).compile(overwrite_output=True)

            run_ffmpeg_with_progress(command, f"Adding subtitles to {path}...")
            print("Video creation completed successfully!")
        except Exception as e:
            print(f"Video processing failed: {str(e)}")
            failures += 1

    return failures

if __name__ == "__main__":
    raise SystemExit(main())

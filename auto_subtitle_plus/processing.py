from __future__ import annotations

import glob
import json
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import ffmpeg
import psutil

from .backends import (
    asr_backend_fingerprint,
    format_backend_error,
    load_backend_model,
    validate_backend_options,
)
from .translation_pipeline import (
    PIPELINE_REVISION,
    TranslationPipeline,
    TranslationStageError,
    atomic_write_text,
    cache_source_transcript,
    default_cache_dir,
    read_cached_source_transcript,
    source_as_subtitle_cues,
    source_cache_key,
    source_cues_from_transcript,
)
from .translation_types import TranslationRequest, TranslationSettings
from .transcription_worker import transcribe_source_worker
from .utils import get_filename, is_audio, write_subtitle, write_txt


ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


class JobCancelled(RuntimeError):
    pass


@dataclass
class ProcessingResult:
    status: str
    outputs: tuple[str, ...] = ()
    error: str | None = None
    warnings: tuple[str, ...] = ()
    failures: int = 0


@dataclass
class ProcessingContext:
    progress: ProgressCallback | None = None
    cancel: CancelCallback | None = None
    temp_dir: str | None = None
    stdout: Callable[[str], None] | None = print
    stderr_limit: int = 40
    warnings: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def emit(
        self,
        state: str,
        message: str = "",
        progress: float | None = None,
        path: str | None = None,
        **details: Any,
    ) -> None:
        event: dict[str, Any] = {
            "state": state,
            "progress": clamp_progress(progress),
            "message": message,
        }
        if path is not None:
            event["path"] = path
        for key, value in details.items():
            if value is not None:
                event[key] = value
        if self.progress is not None:
            self.progress(event)

    def print(self, message: str) -> None:
        if message:
            self.emit("log", str(message), progress=None)
        if self.stdout is not None:
            self.stdout(message)

    def check_cancelled(self) -> None:
        if self.cancel is not None and self.cancel():
            raise JobCancelled("Job cancelled")

    def add_output(self, path: str) -> None:
        self.outputs.append(os.path.abspath(path))

    def add_warning(self, warning: str) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)

    def add_error(self, message: str) -> None:
        if len(self.errors) < self.stderr_limit:
            self.errors.append(message)


def run_job(
    options: Any,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    pipeline: TranslationPipeline | None = None,
    close_translation_runtime_on_finish: bool = True,
    stdout: Callable[[str], None] | None = print,
) -> ProcessingResult:
    context = ProcessingContext(progress=progress, cancel=cancel, stdout=stdout)
    own_pipeline = pipeline is None
    if pipeline is None:
        pipeline = TranslationPipeline(arg_value(options, "translation_cache_dir", None))

    try:
        with tempfile.TemporaryDirectory(prefix="auto-subtitle-plus-") as temp_dir:
            context.temp_dir = temp_dir
            normalized = normalize_processing_options(options)
            validate_processing_options(normalized)
            validate_input_paths(normalized.paths)
            validate_outputs_available(normalized)
            validate_backend_options(normalized, ParserErrorAdapter())
            validate_translation_options(normalized, ParserErrorAdapter())
            context.check_cancelled()
            result = _run_job(normalized, context, pipeline)
            if result.failures:
                error = context.errors[0] if context.errors else f"{result.failures} file operation(s) failed"
                if result.failures > 1 and context.errors:
                    error = f"{error} ({result.failures} failures total)"
                return ProcessingResult(
                    status="failed",
                    outputs=tuple(context.outputs),
                    error=error,
                    warnings=tuple(context.warnings),
                    failures=result.failures,
                )
            context.emit("completed", "Job completed", progress=1.0)
            return ProcessingResult(
                status="completed",
                outputs=tuple(context.outputs),
                warnings=tuple(context.warnings),
            )
    except JobCancelled as error:
        context.emit("cancelled", str(error), progress=None)
        return ProcessingResult(
            status="cancelled",
            outputs=tuple(context.outputs),
            error=str(error),
            warnings=tuple(context.warnings),
        )
    except Exception as error:
        message = format_backend_error(error) if not isinstance(error, ValueError) else str(error)
        context.emit("failed", message, progress=None)
        return ProcessingResult(
            status="failed",
            outputs=tuple(context.outputs),
            error=message,
            warnings=tuple(context.warnings),
            failures=1,
        )
    finally:
        if own_pipeline:
            pipeline.close()
        if close_translation_runtime_on_finish:
            close_translation_runtime()


def _run_job(options: Any, context: ProcessingContext, pipeline: TranslationPipeline) -> ProcessingResult:
    context.check_cancelled()

    translation_requested = bool(options.translate_to or options.retry_translation)
    if translation_requested:
        sources, missing_paths = load_cached_sources(options.paths, options, context)
        failures = 0
        if options.retry_translation and missing_paths:
            for path in missing_paths:
                message = f"Cached source transcript not found for {path}; --retry-translation will not rerun ASR."
                context.add_error(message)
                context.print(message)
            failures += len(missing_paths)
        elif missing_paths:
            audio_paths, audio_failures = get_audio(
                missing_paths,
                options.output_audio,
                options.output_dir,
                options.extract_workers,
                context,
                options,
            )
            worker_sources, source_failures = generate_source_transcripts(audio_paths, None, options, context)
            sources.update(worker_sources)
            failures += audio_failures + source_failures
    else:
        try:
            context.emit("loading", "Loading transcription model", progress=None)
            model = load_backend_model(options)
        except Exception as error:
            raise RuntimeError(f"Model loading failed: {format_backend_error(error)}") from error

        try:
            audio_paths, failures = get_audio(
                options.paths,
                options.output_audio,
                options.output_dir,
                options.extract_workers,
                context,
                options,
            )
            sources, source_failures = generate_source_transcripts(audio_paths, model, options, context)
            failures += source_failures
        finally:
            close_asr_model(model)

    subtitles, subtitle_failures = generate_subtitles_from_sources(sources, options.output_srt, options.output_dir, options, context, pipeline)
    failures += subtitle_failures

    if options.output_video:
        failures += create_subtitled_videos(options.paths, subtitles, options.output_dir, options.output_mkv, context, options)

    return ProcessingResult(status="completed", outputs=tuple(context.outputs), warnings=tuple(context.warnings), failures=failures)


def job_needs_asr(options: Any) -> bool:
    normalized = normalize_processing_options(options)
    if not (normalized.translate_to or normalized.retry_translation):
        return True
    _sources, missing_paths = load_cached_sources(normalized.paths, normalized, ProcessingContext(stdout=None))
    return bool(missing_paths and not normalized.retry_translation)


def normalize_processing_options(options: Any) -> Any:
    values = dict(vars(options)) if hasattr(options, "__dict__") else dict(options)
    paths = values.get("paths")
    if paths is None:
        path = values.get("path")
        paths = [path] if path else []
    input_paths: list[str] = []
    for pattern in paths:
        matches = glob.glob(str(pattern))
        input_paths.extend(matches or [str(pattern)])
    values["paths"] = input_paths
    if values.get("output_dir") is None:
        if len(input_paths) == 1:
            parent = os.path.dirname(os.path.abspath(input_paths[0]))
            values["output_dir"] = parent or os.getcwd()
        else:
            values["output_dir"] = os.getcwd()
    values["output_dir"] = os.path.abspath(values["output_dir"])
    if values.get("device") is None:
        values["device"] = default_device()
    if not values.get("output_video") and not values.get("output_srt") and not values.get("output_txt"):
        values["output_srt"] = True
    if str(values.get("model", "")).endswith(".en"):
        values["language"] = "en"
    if values.get("no_adaptive_layout"):
        values["subtitle_layout"] = "preserve"
    defaults = default_processing_values()
    defaults.update(values)
    return SimpleNamespace(**defaults)


def default_processing_values() -> dict[str, Any]:
    physical_cores = psutil.cpu_count(logical=False) or 2
    return {
        "backend": "stable",
        "model": "small",
        "output_dir": os.getcwd(),
        "output_srt": False,
        "output_audio": False,
        "output_video": False,
        "subtitle_format": "srt",
        "output_txt": False,
        "output_mkv": False,
        "language": None,
        "translate_off": False,
        "translate_to": None,
        "translation_engine": "local",
        "translation_route": "direct",
        "translation_model": None,
        "translation_device": "auto",
        "translation_cache_dir": None,
        "retry_translation": False,
        "offline": False,
        "bilingual": False,
        "output_source_subtitles": False,
        "output_intermediate_subtitles": False,
        "subtitle_layout": "adaptive",
        "no_adaptive_layout": False,
        "batch_size": 10,
        "max_workers": 4,
        "extract_workers": max(1, physical_cores // 2),
        "device": None,
        "compute_type": "auto",
        "inference_batch_size": 1,
        "vad": False,
        "verbose": False,
        "enhance_consistency": False,
        "word_timestamps": False,
        "max_chars_per_line": 42,
        "max_lines": 2,
        "max_cps": 17.0,
        "min_duration": 1.0,
        "max_duration": 7.0,
        "context": "",
        "glossary": None,
        "overwrite": True,
    }


def validate_processing_options(options: Any) -> None:
    if not options.paths:
        raise ValueError("No valid input files found!")
    if options.subtitle_format not in ("srt", "vtt"):
        raise ValueError("--subtitle-format must be srt or vtt")
    for name in ("max_chars_per_line", "max_lines"):
        if int(getattr(options, name)) < 1:
            raise ValueError(f"{name} must be positive")
    for name in ("max_cps", "min_duration", "max_duration"):
        if float(getattr(options, name)) <= 0:
            raise ValueError(f"{name} must be positive")
    if float(options.max_duration) < float(options.min_duration):
        raise ValueError("max_duration must be greater than or equal to min_duration")
    if options.glossary is not None and not isinstance(options.glossary, dict):
        raise ValueError("glossary must be a mapping")


def validate_input_paths(paths: Iterable[str]) -> None:
    for path in paths:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", str(path)):
            raise ValueError(f"Only local file paths are supported: {path}")
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError(f"Input file does not exist: {path}")


def validate_outputs_available(options: Any) -> None:
    outputs = planned_output_paths(options)
    for path in outputs:
        ensure_not_input_path(path, options)
    if options.overwrite:
        return
    existing = [path for path in outputs if os.path.exists(path)]
    if existing:
        raise FileExistsError(f"Output already exists: {existing[0]}")


def ensure_publishable_output(path: str, options: Any) -> None:
    ensure_not_input_path(path, options)
    if not arg_value(options, "overwrite", True) and os.path.exists(path):
        raise FileExistsError(f"Output already exists: {path}")


def ensure_not_input_path(path: str, options: Any) -> None:
    output = Path(path).resolve()
    for input_path in arg_value(options, "paths", ()):
        if output == Path(input_path).resolve():
            raise ValueError(f"Refusing to overwrite input file: {path}")


def planned_output_paths(options: Any) -> list[str]:
    output_dir = options.output_dir
    paths: list[str] = []
    for path in options.paths:
        name = get_filename(path)
        if options.output_srt:
            paths.append(os.path.join(output_dir, f"{name}.{options.subtitle_format}"))
        if options.output_txt:
            paths.append(os.path.join(output_dir, f"{name}.txt"))
        if options.output_audio and not is_audio(path):
            paths.append(os.path.join(output_dir, f"{name}.mp3"))
        if options.output_source_subtitles and options.translate_to and options.language:
            paths.append(os.path.join(output_dir, f"{name}.source.{options.language}.{options.subtitle_format}"))
            if options.output_txt:
                paths.append(os.path.join(output_dir, f"{name}.source.{options.language}.txt"))
        if options.output_intermediate_subtitles and options.translation_route == "via-en":
            paths.append(os.path.join(output_dir, f"{name}.intermediate.en.{options.subtitle_format}"))
            if options.output_txt:
                paths.append(os.path.join(output_dir, f"{name}.intermediate.en.txt"))
        if options.output_video and not is_audio(path):
            ext = "mkv" if options.output_mkv else "mp4"
            paths.append(os.path.join(output_dir, f"{name}_subtitled.{ext}"))
    return paths


def get_audio(paths, save_audio, output_dir, num_workers, context: ProcessingContext | None = None, options: Any | None = None):
    context = context or ProcessingContext()
    successful_audio = {}
    tasks = []
    failures = 0

    for path in paths:
        context.check_cancelled()
        if is_audio(path):
            successful_audio[path] = path
            continue

        target_dir = output_dir if save_audio else context.temp_dir or tempfile.mkdtemp(prefix="auto-subtitle-audio-")
        output_path = os.path.join(target_dir, f"{get_filename(path)}.mp3")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if save_audio:
            ensure_publishable_output(output_path, options or SimpleNamespace(paths=paths, overwrite=True))

        tasks.append((path, output_path))

    if tasks:
        for input_path, output_path in tasks:
            context.check_cancelled()
            context.emit("extracting", f"Extracting audio from {os.path.basename(input_path)}", progress=None, path=input_path, stage="extracting")
            try:
                call_ffmpeg_extract_audio(input_path, output_path, context)
                successful_audio[input_path] = output_path
                if save_audio:
                    context.add_output(output_path)
            except Exception as error:
                message = f"Audio extraction failed for {input_path}: {error}"
                context.add_error(message)
                context.print(message)
                failures += 1

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
    except Exception as error:
        return input_path, output_path, str(error)


def call_ffmpeg_extract_audio(input_path: str, output_path: str, context: ProcessingContext) -> None:
    try:
        ffmpeg_extract_audio(input_path, output_path, context=context)
    except TypeError as error:
        if "unexpected keyword argument 'context'" not in str(error):
            raise
        ffmpeg_extract_audio(input_path, output_path)


def generate_source_transcripts(audio_paths, model, args, context: ProcessingContext | None = None):
    context = context or ProcessingContext()
    sources = {}
    failures = 0

    for path, audio_path in audio_paths.items():
        context.check_cancelled()
        context.print(f"\nProcessing: {os.path.basename(path)}")
        context.emit("transcribing", f"Transcribing {os.path.basename(path)}", progress=None, path=path, stage="transcribing", elapsed=0.0)
        started = time.monotonic()
        try:
            source_key = source_cache_key(path, source_stage_settings(args))
            should_read_cache = bool(arg_value(args, "translate_to", None) or arg_value(args, "retry_translation", False))
            should_write_cache = should_read_cache or bool(arg_value(args, "cache_source_transcript", True))
            if should_read_cache:
                cached = read_cached_source(path, args)
                if cached is not None:
                    sources[path] = cached
                    context.print("Using cached source transcript")
                    context.emit("notice", "Using cached source transcript", progress=None, path=path)
                    continue
            if model is None:
                worker_result = run_transcription_worker(path, audio_path, source_key, args)
                if not worker_result["ok"]:
                    message = f"Transcription failed for {path}: {worker_result['error']}"
                    context.add_error(message)
                    context.print(f"Transcription failed: {worker_result['error']}")
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
            if should_write_cache and cache_settings is not None:
                cache_source_transcript(arg_value(args, "translation_cache_dir", None), source_key, cues, language)
            sources[path] = {"source_key": source_key, "language": language, "cues": cues}
            context.emit(
                "transcribing",
                f"Transcription complete for {os.path.basename(path)}",
                progress=1.0,
                path=path,
                stage="transcribing",
                languages={"source": language},
                elapsed=round(time.monotonic() - started, 3),
            )
        except Exception as error:
            message = f"Transcription failed for {path}: {format_backend_error(error)}"
            context.add_error(message)
            context.print(f"Transcription failed: {format_backend_error(error)}")
            failures += 1
            continue

    return sources, failures


def load_cached_sources(input_paths, args, context: ProcessingContext | None = None):
    context = context or ProcessingContext()
    sources = {}
    missing = []
    for path in input_paths:
        cached = read_cached_source(path, args)
        if cached is None:
            missing.append(path)
        else:
            sources[path] = cached
            context.print(f"Using cached source transcript for: {os.path.basename(path)}")
            context.emit("notice", "Using cached source transcript", progress=None, path=path)
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
    pipeline = TranslationPipeline(arg_value(args, "translation_cache_dir", None))
    try:
        subtitles, subtitle_failures = generate_subtitles_from_sources(sources, output_srt, output_dir, args, None, pipeline)
    finally:
        pipeline.close()
    return subtitles, source_failures + subtitle_failures


def generate_subtitles_from_sources(sources, output_srt, output_dir, args, context: ProcessingContext | None = None, pipeline: TranslationPipeline | None = None):
    context = context or ProcessingContext()
    own_pipeline = pipeline is None
    pipeline = pipeline or TranslationPipeline(arg_value(args, "translation_cache_dir", None))
    subtitles = {}
    failures = 0

    try:
        for path, source in sources.items():
            context.check_cancelled()
            try:
                if arg_value(args, "output_source_subtitles", False) and args.translate_to:
                    write_source_exports(path, source, output_dir, args, context)
                final_cues, source_cues, intermediate_cues, raw_final_cues, raw_intermediate_cues, translation_warnings = output_cues_for_source(source, args, pipeline, context, path)
                print_translation_warnings(path, translation_warnings, context)
                subtitle_filename = f"{get_filename(path)}.{args.subtitle_format}"
                subtitle_dir = output_dir if output_srt else context.temp_dir or tempfile.mkdtemp(prefix="auto-subtitle-subtitles-")
                subtitle_path = os.path.join(subtitle_dir, subtitle_filename)
                context.emit("writing", f"Writing subtitles for {os.path.basename(path)}", progress=None, path=path, stage="writing")
                ensure_publishable_output(subtitle_path, args)
                write_subtitle_atomic(
                    subtitle_path,
                    final_cues,
                    args.subtitle_format,
                    bilingual=args.bilingual and bool(args.translate_to),
                    max_chars_per_line=args.max_chars_per_line,
                )
                subtitles[path] = subtitle_path
                if output_srt:
                    context.add_output(subtitle_path)
                    context.print(f"Subtitles saved to: {os.path.abspath(subtitle_path)}")

                if arg_value(args, "output_intermediate_subtitles", False) and intermediate_cues:
                    intermediate_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.{args.subtitle_format}")
                    ensure_publishable_output(intermediate_path, args)
                    write_subtitle_atomic(intermediate_path, intermediate_cues, args.subtitle_format, False, args.max_chars_per_line)
                    context.add_output(intermediate_path)
                    context.print(f"Intermediate subtitles saved to: {os.path.abspath(intermediate_path)}")
                    if args.output_txt:
                        intermediate_txt_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.txt")
                        ensure_publishable_output(intermediate_txt_path, args)
                        write_txt_atomic(intermediate_txt_path, raw_intermediate_cues or intermediate_cues)
                        context.add_output(intermediate_txt_path)
                        context.print(f"Intermediate transcript saved to: {os.path.abspath(intermediate_txt_path)}")

                if args.output_txt:
                    txt_path = os.path.join(output_dir, f"{get_filename(path)}.txt")
                    ensure_publishable_output(txt_path, args)
                    write_txt_atomic(txt_path, raw_final_cues or final_cues)
                    context.add_output(txt_path)
                    context.print(f"Transcript saved to: {os.path.abspath(txt_path)}")
            except TranslationStageError as error:
                if arg_value(args, "output_intermediate_subtitles", False) and error.intermediate_cues:
                    intermediate_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.{args.subtitle_format}")
                    ensure_publishable_output(intermediate_path, args)
                    write_subtitle_atomic(intermediate_path, error.intermediate_cues, args.subtitle_format, False, args.max_chars_per_line)
                    context.add_output(intermediate_path)
                    context.print(f"Intermediate subtitles saved to: {os.path.abspath(intermediate_path)}")
                    if args.output_txt:
                        intermediate_txt_path = os.path.join(output_dir, f"{get_filename(path)}.intermediate.en.txt")
                        ensure_publishable_output(intermediate_txt_path, args)
                        write_txt_atomic(intermediate_txt_path, error.intermediate_cues)
                        context.add_output(intermediate_txt_path)
                        context.print(f"Intermediate transcript saved to: {os.path.abspath(intermediate_txt_path)}")
                message = f"File write error for {path}: {str(error)}"
                context.add_error(message)
                context.print(f"File write error: {str(error)}")
                failures += 1
            except Exception as error:
                message = f"File write error for {path}: {str(error)}"
                context.add_error(message)
                context.print(f"File write error: {str(error)}")
                failures += 1
    finally:
        if own_pipeline:
            pipeline.close()

    return subtitles, failures


def write_source_exports(path, source, output_dir, args, context: ProcessingContext | None = None):
    context = context or ProcessingContext()
    source_cues = source["cues"]
    source_path = os.path.join(output_dir, f"{get_filename(path)}.source.{source['language']}.{args.subtitle_format}")
    ensure_publishable_output(source_path, args)
    write_subtitle_atomic(source_path, source_as_subtitle_cues(source_cues), args.subtitle_format, False, args.max_chars_per_line)
    context.add_output(source_path)
    context.print(f"Source subtitles saved to: {os.path.abspath(source_path)}")
    if args.output_txt:
        source_txt_path = os.path.join(output_dir, f"{get_filename(path)}.source.{source['language']}.txt")
        ensure_publishable_output(source_txt_path, args)
        write_txt_atomic(source_txt_path, source_cues)
        context.add_output(source_txt_path)
        context.print(f"Source transcript saved to: {os.path.abspath(source_txt_path)}")


def output_cues_for_source(source, args, pipeline, context: ProcessingContext | None = None, path: str | None = None):
    context = context or ProcessingContext()
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
        max_chars_per_line=int(arg_value(args, "max_chars_per_line", 42)),
        max_lines=int(arg_value(args, "max_lines", 2)),
        max_cps=float(arg_value(args, "max_cps", 17.0)),
        min_duration=float(arg_value(args, "min_duration", 1.0)),
        max_duration=float(arg_value(args, "max_duration", 7.0)),
        revision=PIPELINE_REVISION,
    )
    if settings.engine == "local":
        print_translation_selection(settings, source["language"], args.translate_to, args, context)
    context.emit(
        "translating",
        f"Translating {os.path.basename(path or '')}".strip(),
        progress=None,
        path=path,
        stage="translating",
        languages={"source": source["language"], "target": args.translate_to},
    )
    result = pipeline.translate(
        TranslationRequest(
            source_key=source["source_key"],
            cues=source_cues,
            settings=settings,
            context=arg_value(args, "context", ""),
            glossary=arg_value(args, "glossary", None),
            cache_dir=arg_value(args, "translation_cache_dir", None),
            progress=lambda event: emit_translation_progress(event, context, path),
            cancel=context.cancel,
        )
    )
    return result.final_cues, result.source_cues, result.intermediate_cues, result.raw_final_cues, result.raw_intermediate_cues, result.warnings


def print_translation_warnings(path, warnings, context: ProcessingContext | None = None):
    context = context or ProcessingContext()
    for warning in dict.fromkeys(warnings or ()):
        context.add_warning(str(warning))
        context.print(f"Translation warning for {os.path.basename(path)}: {warning}")


def write_subtitle_atomic(path, cues, subtitle_format, bilingual, max_chars_per_line=42):
    buffer = StringIO()
    write_subtitle(
        cues,
        buffer,
        subtitle_format=subtitle_format,
        translate_off=True,
        bilingual=bilingual,
        max_chars_per_line=max_chars_per_line,
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
        except Exception as error:
            parser.error(str(error))


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


def print_translation_selection(settings, source_language, target_language, args, context: ProcessingContext | None = None):
    from .model_manager import model_metadata, resolve_model

    context = context or ProcessingContext()
    if settings.route == "via-en":
        legs = ((source_language, "en"), ("en", target_language))
    else:
        legs = ((source_language, target_language),)
    for source, target in legs:
        spec = resolve_model(settings.model_id or "hy-mt2-1.8b-q8", source, target)
        info = model_metadata(spec, arg_value(args, "translation_cache_dir", None))
        mib = info["download_size"] / (1024 * 1024)
        notice = f" {info['notice']}" if info.get("notice") else ""
        context.print(
            f"Translation model selected: {spec.id} {source}->{target}, "
            f"revision {spec.revision[:12]}, {mib:.1f} MiB, {info['status']}, "
            f"license {spec.license}.{notice}"
        )


def emit_translation_progress(event, context: ProcessingContext, path: str | None) -> None:
    state = event.get("state", "translating")
    message = event.get("message") or event.get("model") or event.get("file") or ""
    progress = event.get("progress")
    if progress is None and event.get("total_bytes", 0) > 0:
        progress = min(1.0, event.get("completed_bytes", 0) / event["total_bytes"])
    if state in ("writing", "completed", "failed", "cancelled", "notice"):
        mapped = state
    elif state in ("downloading", "preparing", "ready", "waiting"):
        mapped = "loading"
    else:
        mapped = "translating"
    context.emit(mapped, message, progress=progress, path=path, stage=state)
    print_translation_progress(event, context)


_last_download_progress = None


def print_translation_progress(event, context: ProcessingContext | None = None):
    global _last_download_progress
    context = context or ProcessingContext()
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
        context.print(f"Translation {state}: {message}")
    else:
        context.print(f"Translation {state}: {progress:.0%} {message}")


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


def create_subtitled_videos(input_paths, subtitles, output_dir, output_mkv, context: ProcessingContext | None = None, options: Any | None = None):
    context = context or ProcessingContext()
    failures = 0
    for path in input_paths:
        context.check_cancelled()
        if is_audio(path) or path not in subtitles:
            continue

        subtitle_path = subtitles[path]
        output_ext = "mkv" if output_mkv else "mp4"
        output_filename = f"{get_filename(path)}_subtitled.{output_ext}"
        output_path = os.path.join(output_dir, output_filename)
        os.makedirs(output_dir, exist_ok=True)
        ensure_publishable_output(output_path, options or SimpleNamespace(paths=input_paths, overwrite=True))

        context.print(f"\nCreating subtitled video: {os.path.abspath(output_path)}")
        context.emit("encoding", f"Creating subtitled video {output_filename}", progress=None, path=path, stage="encoding")

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

            run_ffmpeg_with_progress(command, f"Adding subtitles to {path}...", context=context)
            context.add_output(output_path)
            context.print("Video creation completed successfully!")
        except Exception as error:
            message = f"Video processing failed for {path}: {str(error)}"
            context.add_error(message)
            context.print(f"Video processing failed: {str(error)}")
            failures += 1

    return failures


def run_ffmpeg_with_progress(cmd_args, description, context: ProcessingContext | None = None):
    context = context or ProcessingContext()
    context.print(description)
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        cmd_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )

    duration = None
    stderr_output = []
    try:
        if process.stderr:
            for line in process.stderr:
                context.check_cancelled()
                line = line.strip()
                stderr_output.append(line)
                del stderr_output[:-context.stderr_limit]

                if "Duration:" in line:
                    duration_match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", line)
                    if duration_match:
                        hours, minutes, seconds = duration_match.groups()
                        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

                elif "time=" in line and duration:
                    time_match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                    if time_match:
                        hours, minutes, seconds = time_match.groups()
                        current_time = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                        progress = min(current_time / duration, 1.0)
                        context.emit("extracting", f"{progress:.1%}", progress=progress, stage="ffmpeg")
                        context.print(f"\rProgress: {progress * 100:.1f}%")

        process.wait()
    except JobCancelled:
        terminate_process_tree(process.pid)
        raise
    if process.returncode != 0:
        context.print(f"\nFFmpeg error (exit code {process.returncode}):")
        for line in stderr_output[-10:]:
            context.print(line)
        raise subprocess.CalledProcessError(process.returncode, cmd_args, "\n".join(stderr_output))

    context.emit("extracting", "Complete", progress=1.0, stage="ffmpeg")
    context.print("\rProgress: 100.0% - Complete!                ")


def ffmpeg_extract_audio(input_path, output_path, context: ProcessingContext | None = None):
    cmd_args = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-async",
        "1",
        output_path,
    ]
    run_ffmpeg_with_progress(cmd_args, f"Extracting audio from {input_path}...", context=context)


def terminate_process_tree(pid: int | None) -> None:
    if not pid:
        return
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    children = parent.children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        parent.terminate()
    except psutil.Error:
        pass
    gone, alive = psutil.wait_procs([parent, *children], timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass


def clamp_progress(progress: float | None) -> float | None:
    if progress is None:
        return None
    return max(0.0, min(1.0, float(progress)))


class ParserErrorAdapter:
    def error(self, message: str) -> None:
        raise ValueError(message)


def parse_glossary(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    path = Path(value)
    if path.exists():
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    else:
        data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("--glossary must be a JSON object or a path to one")
    return {str(key): str(item) for key, item in data.items()}

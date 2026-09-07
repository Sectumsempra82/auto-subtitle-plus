import argparse
import gc
import json
import math
import os
import re
import subprocess
import sys
import time
from typing import Any, Iterable

from .backends import (
    BACKENDS,
    format_backend_error,
    load_backend_model,
    print_backend_models,
    positive_int,
    resolve_model_language,
    validate_backend_options,
)
from .utils import get_segment_value, normalize_segments


SDH_CUE_PATTERN = re.compile(r"\s*[\[(][^\])]*[\])]\s*")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark stable-ts Whisper models against a reference SRT.",
        epilog=(
            "Reference SRT selection uses full containment: a cue is included only "
            "when its start and end are inside the benchmark audio window after "
            "--reference-offset is applied. Use a pretrimmed reference SRT or pass "
            "--reference-offset when the audio clip is cut from a longer source."
        ),
    )
    parser.add_argument("audio", nargs="?", help="Benchmark audio clip path")
    parser.add_argument(
        "--reference-srt",
        help="Reference SRT path. Can be pretrimmed or the full source SRT.",
    )
    parser.add_argument(
        "--output-json",
        help="Path to write the JSON benchmark report.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["small", "turbo", "large-v3"],
        help="stable-ts/openai-whisper model names to benchmark.",
    )
    parser.add_argument(
        "--backend",
        default="stable",
        choices=BACKENDS,
        help="Transcription backend (default: %(default)s).",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List models for the selected backend and exit without loading a model.",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        help="Torch device passed to stable_whisper.load_model.",
    )
    parser.add_argument(
        "--compute-type",
        default="auto",
        help="faster-whisper compute type (default: %(default)s).",
    )
    parser.add_argument(
        "--inference-batch-size",
        type=positive_int,
        default=1,
        help="faster-whisper inference batch size; values > 1 use BatchedInferencePipeline and require --vad (default: %(default)s).",
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        help="Enable faster-whisper VAD filtering.",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language passed to transcription (default: %(default)s).",
    )
    parser.add_argument(
        "--reference-offset",
        type=float,
        default=0.0,
        help="Seconds from the full reference timeline where the audio clip starts.",
    )
    parser.add_argument(
        "--timestamp-tolerance",
        type=float,
        default=0.5,
        help="Allowed seconds past clip duration for final segment timestamps.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show stable-ts transcription details.",
    )
    parser.add_argument(
        "--enhance-consistency",
        action="store_true",
        help="Pass condition_on_previous_text=True; unsupported with faster batched inference.",
    )
    parser.add_argument(
        "--word-timestamps",
        action="store_true",
        help="Pass word_timestamps=True to stable-ts transcription.",
    )
    args = parser.parse_args()
    if args.list_models:
        return args
    if not args.audio:
        parser.error("audio is required unless --list-models is used")
    if not args.reference_srt:
        parser.error("--reference-srt is required unless --list-models is used")
    if not args.output_json:
        parser.error("--output-json is required unless --list-models is used")
    args.requested_models = list(args.models)
    validate_args(args, parser)
    validate_backend_options(args, parser)
    return args


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not math.isfinite(args.reference_offset) or args.reference_offset < 0:
        parser.error("--reference-offset must be a finite value >= 0")
    if not math.isfinite(args.timestamp_tolerance) or args.timestamp_tolerance < 0:
        parser.error("--timestamp-tolerance must be a finite value >= 0")


def probe_duration(path: str) -> float | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None

    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def strip_sdh_cues(text: str) -> str:
    text = SDH_CUE_PATTERN.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def parse_srt(path: str) -> list[dict[str, Any]]:
    try:
        import srt
    except ImportError as exc:
        raise RuntimeError(
            "Install the benchmark extra to parse reference SRT files: "
            "pip install -e .[benchmark]"
        ) from exc

    with open(path, "r", encoding="utf-8-sig") as file:
        content = file.read()

    cues = []
    for subtitle in srt.parse(content, ignore_errors=False):
        start = subtitle.start.total_seconds()
        end = subtitle.end.total_seconds()
        text = strip_sdh_cues(subtitle.content.replace("\n", " "))
        if not text:
            continue
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError(f"Invalid SRT cue timestamp at index {subtitle.index}")

        cues.append(
            {
                "start": start,
                "end": end,
                "text": text,
            }
        )
    return cues


def reference_text_for_clip(
    cues: Iterable[dict[str, Any]],
    offset: float,
    duration: float | None,
) -> tuple[str, list[dict[str, Any]]]:
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("offset must be a finite value >= 0")
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be a finite value > 0")

    clip_end = offset + duration
    selected = [
        {
            **cue,
            "text": strip_sdh_cues(str(cue["text"])),
        }
        for cue in cues
        if cue["start"] >= offset and cue["end"] <= clip_end
    ]

    return " ".join(cue["text"] for cue in selected), [
        {
            "start": round(cue["start"] - offset, 3),
            "end": round(cue["end"] - offset, 3),
            "original_start": round(cue["start"], 3),
            "original_end": round(cue["end"], 3),
            "text": cue["text"],
        }
        for cue in selected
    ]


def normalize_for_wer(text: str) -> str:
    text = strip_sdh_cues(text).lower()
    text = re.sub(r"[^\w\s']", " ", text)
    return " ".join(text.split())


def calculate_wer(reference: str, hypothesis: str) -> tuple[float | None, str | None]:
    try:
        from jiwer import wer
    except ImportError:
        return None, "Install the benchmark extra to enable WER: pip install -e .[benchmark]"

    return wer(normalize_for_wer(reference), normalize_for_wer(hypothesis)), None


def validate_segments(
    segments: list[Any],
    duration: float | None,
    tolerance: float,
) -> dict[str, Any]:
    invalid = []
    previous_start = -math.inf
    for index, segment in enumerate(segments):
        try:
            start = float(get_segment_value(segment, "start"))
            end = float(get_segment_value(segment, "end"))
            text = str(get_segment_value(segment, "text")).strip()
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            invalid.append({"index": index, "reason": f"unreadable segment: {exc}"})
            continue

        if not text:
            invalid.append({"index": index, "reason": "empty text"})
        if not math.isfinite(start) or not math.isfinite(end):
            invalid.append({"index": index, "reason": "start and end must be finite"})
            continue
        if start < 0:
            invalid.append({"index": index, "reason": "negative start"})
        if end <= start:
            invalid.append({"index": index, "reason": "end must be greater than start"})
        if start < previous_start:
            invalid.append({"index": index, "reason": "start timestamp moved backward"})
        if duration is not None and end > duration + tolerance:
            invalid.append({"index": index, "reason": "end past clip duration"})
        previous_start = start

    return {
        "nonempty_segments": len(segments) > 0,
        "timestamp_valid": not invalid and len(segments) > 0,
        "invalid_segments": invalid,
    }


def serialize_segments(segments: list[Any]) -> list[dict[str, Any]]:
    serialized = []
    for segment in segments:
        serialized.append(
            {
                "start": round(float(get_segment_value(segment, "start")), 3),
                "end": round(float(get_segment_value(segment, "end")), 3),
                "text": str(get_segment_value(segment, "text")).strip(),
            }
        )
    return serialized


def model_error_result(
    model_name: str,
    started_at: float,
    error: Exception,
    requested_model_name: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    message = format_backend_error(error)
    lower_message = message.lower()
    return {
        "model": requested_model_name or model_name,
        "effective_model": model_name,
        "backend": backend,
        "status": "error",
        "total_wall_time_seconds": round(time.perf_counter() - started_at, 3),
        "model_load_wall_time_seconds": None,
        "inference_wall_time_seconds": None,
        "real_time_factor": None,
        "error": message,
        "oom": "out of memory" in lower_message or "cuda" in lower_message and "memory" in lower_message,
    }


def benchmark_model(
    model_name: str,
    audio_path: str,
    reference_text: str,
    duration: float | None,
    args: argparse.Namespace,
    requested_model_name: str | None = None,
) -> dict[str, Any]:
    load_started_at = time.perf_counter()
    model = None
    result = None
    try:
        model_args = argparse.Namespace(**vars(args))
        model_args.model = model_name
        model = load_backend_model(model_args)
        load_finished_at = time.perf_counter()
        inference_started_at = time.perf_counter()
        result = model.transcribe(
            audio_path,
            language=args.language,
            verbose=args.verbose,
            condition_on_previous_text=getattr(args, "enhance_consistency", False),
            word_timestamps=getattr(args, "word_timestamps", False),
        )
        inference_finished_at = time.perf_counter()
        load_time = load_finished_at - load_started_at
        transcribe_time = inference_finished_at - inference_started_at
        total_wall_time = time.perf_counter() - load_started_at
        segments = normalize_segments(result)
        timestamp_adjustments = (
            result.get("timestamp_adjustments")
            if isinstance(result, dict)
            else None
        )
        timestamp_warning = (
            result.get("timestamp_warning")
            if isinstance(result, dict)
            else None
        )
        hypothesis = " ".join(
            str(get_segment_value(segment, "text")).strip()
            for segment in segments
        )
        segment_validation = validate_segments(
            segments,
            duration,
            args.timestamp_tolerance,
        )
        word_error_rate, wer_error = calculate_wer(reference_text, hypothesis)
        status = "ok"
        if wer_error or not segment_validation["timestamp_valid"]:
            status = "failed"

        return {
            "model": requested_model_name or model_name,
            "effective_model": model_name,
            "backend": args.backend,
            "status": status,
            "total_wall_time_seconds": round(total_wall_time, 3),
            "model_load_wall_time_seconds": round(load_time, 3),
            "inference_wall_time_seconds": round(transcribe_time, 3),
            "real_time_factor": round(transcribe_time / duration, 3),
            "wer": round(word_error_rate, 4) if word_error_rate is not None else None,
            "wer_error": wer_error,
            "segment_count": len(segments),
            "timestamp_adjustments": timestamp_adjustments,
            "timestamp_warning": timestamp_warning,
            "segments": serialize_segments(segments),
            **segment_validation,
        }
    except Exception as exc:
        return model_error_result(
            model_name,
            load_started_at,
            exc,
            requested_model_name=requested_model_name,
            backend=args.backend,
        )
    finally:
        del result
        del model
        gc.collect()
        try:
            import torch

            if getattr(torch, "cuda", None) and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def main() -> int:
    args = parse_args()
    if getattr(args, "list_models", False):
        try:
            print_backend_models(args.backend)
        except Exception as exc:
            print(format_backend_error(exc), file=sys.stderr)
            return 1
        return 0
    resolve_model_language(args)

    audio_path = os.path.abspath(args.audio)
    reference_srt_path = os.path.abspath(args.reference_srt)
    output_json_path = os.path.abspath(args.output_json)

    duration = probe_duration(audio_path)
    report = {
        "audio": audio_path,
        "reference_srt": reference_srt_path,
        "reference_offset_seconds": args.reference_offset,
        "reference_selection": "full_containment",
        "duration_seconds": round(duration, 3) if duration else None,
        "backend": args.backend,
        "device": args.device,
        "compute_type": args.compute_type if args.backend == "faster" else None,
        "inference_batch_size": args.inference_batch_size if args.backend == "faster" else None,
        "vad": args.vad if args.backend == "faster" else None,
        "language": args.language,
        "models": [],
    }
    if duration is None or not math.isfinite(duration) or duration <= 0:
        report["setup_error"] = "Audio duration probe failed or returned a non-positive duration."
        write_report(output_json_path, report)
        print(f"Audio duration is invalid. Report saved to: {output_json_path}", file=sys.stderr)
        return 2

    try:
        reference_cues = parse_srt(reference_srt_path)
    except Exception as exc:
        report["setup_error"] = f"Reference SRT parse failed: {exc}"
        write_report(output_json_path, report)
        print(f"Reference SRT parse failed. Report saved to: {output_json_path}", file=sys.stderr)
        return 2

    reference_text, reference_segments = reference_text_for_clip(
        reference_cues,
        args.reference_offset,
        duration,
    )
    report["reference_segment_count"] = len(reference_segments)
    report["reference_segments"] = reference_segments

    if not reference_text:
        report["setup_error"] = "No reference SRT cues matched the clip window."
        write_report(output_json_path, report)
        print(f"No reference text matched. Report saved to: {output_json_path}", file=sys.stderr)
        return 2

    write_report(output_json_path, report)
    requested_models = getattr(args, "requested_models", list(args.models))
    for requested_model_name, model_name in zip(requested_models, args.models):
        print(f"Benchmarking {requested_model_name} on {args.device}...")
        result = benchmark_model(
            model_name,
            audio_path,
            reference_text,
            duration,
            args,
            requested_model_name,
        )
        report["models"].append(result)
        write_report(output_json_path, report)
        if result["status"] == "ok":
            print(
                f"{requested_model_name}: {result['inference_wall_time_seconds']}s transcription, "
                f"RTF={result['real_time_factor']}, WER={result['wer']}"
            )
        else:
            reasons = "; ".join(
                item["reason"] for item in result.get("invalid_segments", [])
            )
            detail = result.get("error") or result.get("wer_error") or reasons
            print(f"{requested_model_name}: {result['status']} - {detail}")

    failed_models = [model for model in report["models"] if model["status"] != "ok"]
    print(f"Benchmark report saved to: {output_json_path}")
    return 1 if failed_models else 0


def write_report(path: str, report: dict[str, Any]) -> None:
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())

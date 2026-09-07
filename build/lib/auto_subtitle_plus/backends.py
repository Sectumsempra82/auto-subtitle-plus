import argparse
import gc
import hashlib
from importlib import metadata
import math
import os
import sys
from typing import Any

from .cuda_runtime import configure_windows_cuda


BACKENDS = ("stable", "faster")
DISTIL_LARGE_V35_ALIAS = "distil-large-v3.5"
DISTIL_LARGE_V35_MODEL = "distil-whisper/distil-large-v3.5-ct2"
DISTIL_LARGE_V35_SOURCE = "https://huggingface.co/distil-whisper/distil-large-v3.5"
ENGLISH_LANGUAGE_VALUES = ("en", "english")
UNKNOWN_ASR_FINGERPRINT = "unknown"


class LazyModule:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.module = None

    def __getattr__(self, name: str) -> Any:
        if self.module is None:
            self.module = __import__(self.module_name)
        return getattr(self.module, name)


stable_whisper = LazyModule("stable_whisper")
whisper = LazyModule("whisper")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def validate_backend_options(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    resolve_model_language(args, parser)
    if args.backend == "stable":
        models = [args.model] if hasattr(args, "model") else list(args.models)
        invalid_models = [model for model in models if model not in whisper.available_models()]
        if invalid_models:
            parser.error(
                "--model/--models must be one of whisper.available_models() "
                f"when --backend stable; invalid: {', '.join(invalid_models)}"
            )
        if args.compute_type != "auto":
            parser.error("--compute-type is only supported with --backend faster")
        if args.inference_batch_size != 1:
            parser.error("--inference-batch-size is only supported with --backend faster")
        if args.vad:
            parser.error("--vad is only supported with --backend faster")
    elif args.inference_batch_size > 1:
        if not args.vad:
            parser.error("--inference-batch-size > 1 with --backend faster requires --vad")
        if getattr(args, "enhance_consistency", False):
            parser.error(
                "--enhance-consistency is not supported with faster batched inference"
            )


def resolve_model_language(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser | None = None,
) -> argparse.Namespace:
    model_names = [args.model] if hasattr(args, "model") else list(args.models)
    resolved = [resolve_model_name(args.backend, model, parser) for model in model_names]
    if any(is_distil_large_v35(model) for model in model_names):
        language = getattr(args, "language", None)
        if language is None:
            args.language = "en"
        elif language.lower() in ENGLISH_LANGUAGE_VALUES:
            args.language = "en"
        else:
            backend_option_error(
                parser,
                f"{DISTIL_LARGE_V35_ALIAS} is English-only; use --language en or omit --language"
            )

    if hasattr(args, "model"):
        args.model = resolved[0]
    else:
        args.models = resolved
    return args


def resolve_model_name(
    backend: str,
    model_name: str,
    parser: argparse.ArgumentParser | None,
) -> str:
    if not is_distil_large_v35(model_name):
        return model_name
    if backend != "faster":
        backend_option_error(
            parser,
            f"{DISTIL_LARGE_V35_ALIAS} requires --backend faster; stable uses OpenAI Whisper model names"
        )
    return DISTIL_LARGE_V35_MODEL


def backend_option_error(parser: argparse.ArgumentParser | None, message: str) -> None:
    if parser is None:
        raise ValueError(message)
    parser.error(message)


def is_distil_large_v35(model_name: str) -> bool:
    return model_name in (DISTIL_LARGE_V35_ALIAS, DISTIL_LARGE_V35_MODEL)


def list_backend_models(backend: str) -> list[str]:
    if backend == "stable":
        return list(whisper.available_models())

    try:
        import faster_whisper
    except ImportError as exc:
        raise RuntimeError(
            "Install the faster-whisper extra to list faster models: "
            "pip install -e .[faster]"
        ) from exc

    models = list(faster_whisper.available_models())
    if DISTIL_LARGE_V35_MODEL not in models:
        models.append(DISTIL_LARGE_V35_MODEL)
    if DISTIL_LARGE_V35_ALIAS not in models:
        models.append(DISTIL_LARGE_V35_ALIAS)
    return models


def print_backend_models(backend: str) -> None:
    print(f"{backend} backend models:")
    for model in list_backend_models(backend):
        if model == DISTIL_LARGE_V35_ALIAS:
            print(
                f"  {model} -> {DISTIL_LARGE_V35_MODEL} "
                f"(English-only, source: {DISTIL_LARGE_V35_SOURCE})"
            )
        elif model == DISTIL_LARGE_V35_MODEL:
            print(f"  {model} (English-only, source: {DISTIL_LARGE_V35_SOURCE})")
        else:
            print(f"  {model}")


def load_backend_model(args: argparse.Namespace) -> Any:
    if args.backend == "stable":
        model_name = args.model
        if getattr(args, "offline", False):
            model_name = ensure_stable_model_offline_available(args.model)
        return StableBackend(stable_whisper.load_model(model_name, device=args.device))

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Install the faster-whisper extra to use --backend faster: "
            "pip install -e .[faster]"
        ) from exc

    if args.device in ("cuda", "auto"):
        configure_windows_cuda()

    model_name = args.model
    model_options = {
        "device": args.device,
        "compute_type": args.compute_type,
    }
    if getattr(args, "offline", False):
        model_options["local_files_only"] = True
    return FasterBackend(
        WhisperModel(
            model_name,
            **model_options,
        ),
        inference_batch_size=args.inference_batch_size,
        vad=args.vad,
    )


def format_backend_error(error: Exception) -> str:
    message = str(error)
    lower_message = message.lower()
    if "cublas64_12.dll" in lower_message or "cudnn" in lower_message:
        return (
            "faster-whisper CUDA runtime is missing NVIDIA CUDA 12/cuDNN runtime DLLs "
            "in this environment. Install the runtime in the active environment, or "
            "retry with --device cpu and an int8 compute type. Original error: "
            f"{message}"
        )
    return message


def asr_backend_fingerprint(args: argparse.Namespace) -> dict[str, Any]:
    package_names = ("faster-whisper", "ctranslate2") if args.backend == "faster" else ("stable-ts", "openai-whisper")
    fingerprint = {
        "backend": args.backend,
        "model": args.model,
        "package": {
            package_name: package_version(package_name)
            for package_name in package_names
        },
    }
    if args.backend == "stable":
        fingerprint.update(stable_model_fingerprint(args.model))
    elif args.backend == "faster":
        fingerprint.update(faster_model_fingerprint(args.model))
    return fingerprint


def package_version(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return UNKNOWN_ASR_FINGERPRINT


def stable_model_fingerprint(model_name: str) -> dict[str, Any]:
    if os.path.isfile(model_name):
        digest = file_sha256(model_name)
        return {
            "fingerprint_available": True,
            "model_source": "local-file",
            "model_path": os.path.abspath(model_name),
            "model_revision": digest,
            "model_sha256": digest,
        }
    if os.path.isdir(model_name):
        model_file = first_existing_file(model_name, ("model.pt", "pytorch_model.bin"))
        digest = file_sha256(model_file) if model_file else UNKNOWN_ASR_FINGERPRINT
        return {
            "fingerprint_available": model_file is not None,
            "model_source": "local-directory",
            "model_path": os.path.abspath(model_name),
            "model_revision": digest,
            "model_sha256": digest,
        }
    try:
        url = whisper._MODELS[model_name]
        official_sha256 = whisper_model_sha256(url)
        cached_path = cached_whisper_model_path(url)
        if not os.path.exists(cached_path):
            return {
                "fingerprint_available": True,
                "model_source": "whisper-cache",
                "model_revision": official_sha256,
                "official_sha256": official_sha256,
                "model_sha256": official_sha256,
                "verified_local_sha256": UNKNOWN_ASR_FINGERPRINT,
            }
        cached_sha256 = file_sha256(cached_path)
        if cached_sha256 != official_sha256:
            return {
                "fingerprint_available": False,
                "model_source": "whisper-cache",
                "model_revision": official_sha256,
                "official_sha256": official_sha256,
                "model_sha256": UNKNOWN_ASR_FINGERPRINT,
            }
        return {
            "fingerprint_available": True,
            "model_source": "whisper-cache",
            "model_revision": official_sha256,
            "official_sha256": official_sha256,
            "model_sha256": cached_sha256,
            "verified_local_sha256": cached_sha256,
        }
    except Exception:
        return {
            "fingerprint_available": False,
            "model_source": UNKNOWN_ASR_FINGERPRINT,
            "model_revision": UNKNOWN_ASR_FINGERPRINT,
            "model_sha256": UNKNOWN_ASR_FINGERPRINT,
        }


def faster_model_fingerprint(model_name: str) -> dict[str, Any]:
    if os.path.isdir(model_name):
        model_file = first_existing_file(model_name, ("model.bin", "model.bin.index.json"))
        digest = file_sha256(model_file) if model_file else UNKNOWN_ASR_FINGERPRINT
        return {
            "fingerprint_available": model_file is not None,
            "model_source": "local-directory",
            "model_path": os.path.abspath(model_name),
            "model_revision": digest,
            "model_sha256": digest,
        }
    if os.path.isfile(model_name):
        digest = file_sha256(model_name)
        return {
            "fingerprint_available": True,
            "model_source": "local-file",
            "model_path": os.path.abspath(model_name),
            "model_revision": digest,
            "model_sha256": digest,
        }
    repo_id = faster_repo_id(model_name)
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return {
            "fingerprint_available": False,
            "model_source": "huggingface-cache",
            "repo_id": repo_id,
            "model_revision": UNKNOWN_ASR_FINGERPRINT,
            "model_sha256": UNKNOWN_ASR_FINGERPRINT,
        }
    try:
        cached_model = try_to_load_from_cache(repo_id, "model.bin")
    except Exception:
        cached_model = None
    if not isinstance(cached_model, str) or not os.path.isfile(cached_model):
        return {
            "fingerprint_available": False,
            "model_source": "huggingface-cache",
            "repo_id": repo_id,
            "model_revision": UNKNOWN_ASR_FINGERPRINT,
            "model_sha256": UNKNOWN_ASR_FINGERPRINT,
        }
    revision = hf_snapshot_revision(cached_model)
    return {
        "fingerprint_available": revision != UNKNOWN_ASR_FINGERPRINT,
        "model_source": "huggingface-cache",
        "repo_id": repo_id,
        "model_revision": revision,
        "model_sha256": file_sha256(cached_model),
    }


def faster_repo_id(model_name: str) -> str:
    if "/" in model_name:
        return model_name
    try:
        from faster_whisper.utils import _MODELS
        return _MODELS.get(model_name, model_name)
    except ImportError:
        return model_name


def hf_snapshot_revision(path: str) -> str:
    parts = os.path.normpath(path).split(os.sep)
    if "snapshots" in parts:
        index = parts.index("snapshots")
        if index + 1 < len(parts):
            return parts[index + 1]
    return UNKNOWN_ASR_FINGERPRINT


def first_existing_file(directory: str, names: tuple[str, ...]) -> str | None:
    for name in names:
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def ensure_stable_model_offline_available(model_name: str) -> str:
    if os.path.isdir(model_name) or os.path.isfile(model_name):
        return model_name
    if model_name not in whisper._MODELS:
        raise RuntimeError(
            f"Stable backend offline mode requires a local model path or cached Whisper model; unknown model: {model_name}"
        )
    url = whisper._MODELS[model_name]
    cache_dir = whisper_cache_dir()
    expected = cached_whisper_model_path(url)
    if not os.path.exists(expected):
        raise RuntimeError(
            f"Whisper model {model_name!r} is not installed in {cache_dir}; offline mode forbids ASR downloads"
        )
    expected_sha256 = whisper_model_sha256(url)
    actual_sha256 = file_sha256(expected)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"Cached Whisper model {model_name!r} failed SHA256 verification; offline mode forbids redownload"
        )
    return expected


def whisper_model_sha256(url: str) -> str:
    parts = url.split("/")
    if len(parts) < 2 or len(parts[-2]) != 64:
        raise RuntimeError("Whisper model URL does not include an expected SHA256 digest")
    return parts[-2]


def cached_whisper_model_path(url: str) -> str:
    return os.path.join(whisper_cache_dir(), os.path.basename(url))


def whisper_cache_dir() -> str:
    default_cache = os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(os.getenv("XDG_CACHE_HOME", default_cache), "whisper")


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StableBackend:
    def __init__(self, model: Any):
        self.model = model

    def transcribe(
        self,
        audio_path: str,
        language: str | None,
        verbose: bool,
        condition_on_previous_text: bool,
        word_timestamps: bool,
    ) -> Any:
        return self.model.transcribe(
            audio_path,
            language=language,
            verbose=verbose,
            condition_on_previous_text=condition_on_previous_text,
            word_timestamps=word_timestamps,
        )

    def close(self) -> None:
        self.model = None
        release_gpu_memory()


class FasterBackend:
    def __init__(self, model: Any, inference_batch_size: int, vad: bool):
        self.model = model
        self.inference_batch_size = inference_batch_size
        self.vad = vad

    def transcribe(
        self,
        audio_path: str,
        language: str | None,
        verbose: bool,
        condition_on_previous_text: bool,
        word_timestamps: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        transcribe_kwargs = {
            "language": language,
            "condition_on_previous_text": condition_on_previous_text,
            "word_timestamps": word_timestamps,
            "vad_filter": self.vad,
        }
        if self.inference_batch_size > 1:
            return self._transcribe_batched(audio_path, transcribe_kwargs, verbose)

        segments, info = self.model.transcribe(audio_path, **transcribe_kwargs)
        return self._segments_result(list(segments), info, verbose)

    def _transcribe_batched(
        self,
        audio_path: str,
        transcribe_kwargs: dict[str, Any],
        verbose: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        try:
            from faster_whisper import BatchedInferencePipeline
        except ImportError as exc:
            raise RuntimeError(
                "Install the faster-whisper extra to use batched inference: "
                "pip install -e .[faster]"
            ) from exc

        pipeline = BatchedInferencePipeline(model=self.model)
        segments, info = pipeline.transcribe(
            audio_path,
            batch_size=self.inference_batch_size,
            **transcribe_kwargs,
        )
        return self._segments_result(list(segments), info, verbose)

    def _segments_result(
        self,
        segments: list[Any],
        info: Any,
        verbose: bool,
    ) -> dict[str, Any]:
        duration = getattr(info, "duration", None) if info is not None else None
        if duration is None:
            raise ValueError("faster-whisper did not report audio duration")
        normalized_segments, adjustment_count = normalize_faster_segments(segments, duration)
        if verbose:
            for segment in normalized_segments:
                print(f"[{segment['start']:.2f}s -> {segment['end']:.2f}s] {segment['text']}")
        if adjustment_count:
            print(
                f"Adjusted or dropped {adjustment_count} faster-whisper timestamp value(s) "
                "to fit the reported media duration.",
                file=sys.stderr,
            )
        result = {
            "segments": normalized_segments,
            "timestamp_adjustments": adjustment_count,
        }
        detected_language = getattr(info, "language", None) if info is not None else None
        if detected_language:
            result["language"] = detected_language
            result["detected_language"] = detected_language
        if adjustment_count:
            result["timestamp_warning"] = (
                f"Adjusted or dropped {adjustment_count} faster-whisper timestamp value(s) "
                "to fit the reported media duration."
            )
        return result

    def close(self) -> None:
        unload = getattr(self.model, "unload_model", None)
        if unload is not None:
            unload()
        self.model = None
        release_gpu_memory()


def release_gpu_memory() -> None:
    gc.collect()
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def normalize_faster_segments(
    segments: list[Any],
    duration: float | None,
) -> tuple[list[dict[str, Any]], int]:
    finite_duration = None
    if duration is not None:
        finite_duration = float(duration)
        if not math.isfinite(finite_duration) or finite_duration <= 0:
            raise ValueError("faster-whisper returned a non-finite or non-positive audio duration")

    normalized = []
    adjustment_count = 0
    for segment in segments:
        item, adjustments = faster_segment_to_dict(segment, finite_duration)
        adjustment_count += adjustments
        if item is not None:
            normalized.append(item)
    return normalized, adjustment_count


def faster_segment_to_dict(
    segment: Any,
    duration: float | None,
) -> tuple[dict[str, Any] | None, int]:
    start, end, adjustments = bounded_timestamp_pair(
        segment.start,
        segment.end,
        duration,
    )
    if start is None or end is None:
        return None, adjustments

    item = {
        "start": start,
        "end": end,
        "text": segment.text,
    }
    words = getattr(segment, "words", None)
    if words:
        normalized_words = []
        for word in words:
            word_start, word_end, word_adjustments = bounded_timestamp_pair(
                word.start,
                word.end,
                duration,
            )
            adjustments += word_adjustments
            if word_start is None or word_end is None:
                continue
            normalized_words.append(
                {
                    "start": word_start,
                    "end": word_end,
                    "word": word.word,
                }
            )
        if normalized_words:
            item["words"] = normalized_words
    return item, adjustments


def bounded_timestamp_pair(
    start: float,
    end: float,
    duration: float | None,
) -> tuple[float | None, float | None, int]:
    adjustment_count = 0
    start = float(start)
    end = float(end)
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("faster-whisper returned a non-finite timestamp")
    if duration is None:
        return start, end, adjustment_count
    if end <= 0 or start >= duration:
        return None, None, adjustment_count + 1

    bounded_start = max(0.0, min(start, duration))
    bounded_end = max(0.0, min(end, duration))
    if bounded_start != start or bounded_end != end:
        adjustment_count += 1
    if bounded_end <= bounded_start:
        return None, None, adjustment_count + 1
    return bounded_start, bounded_end, adjustment_count

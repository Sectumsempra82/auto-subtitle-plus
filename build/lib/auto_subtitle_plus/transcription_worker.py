from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .backends import format_backend_error, load_backend_model
from .translation_pipeline import source_cue_to_dict, source_cues_from_transcript


def transcribe_source_worker(
    input_path: str,
    audio_path: str,
    args_dict: dict[str, Any],
    source_key: str,
) -> dict[str, Any]:
    args = SimpleNamespace(**args_dict)
    model = None
    try:
        model = load_backend_model(args)
        result = model.transcribe(
            audio_path,
            language=args.language,
            verbose=args.verbose,
            condition_on_previous_text=args.enhance_consistency,
            word_timestamps=args.word_timestamps,
        )
        cues = source_cues_from_transcript(result, source_key, args.language)
        language = cues[0].language if cues else "unknown"
        return {
            "ok": True,
            "input_path": input_path,
            "source_key": source_key,
            "language": language,
            "cues": [source_cue_to_dict(cue) for cue in cues],
        }
    except Exception as error:
        return {
            "ok": False,
            "input_path": input_path,
            "error": format_backend_error(error),
        }
    finally:
        if model is not None:
            model.close()

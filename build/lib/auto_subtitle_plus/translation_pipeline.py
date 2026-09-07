from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .utils import get_segment_value, normalize_segments
from .translation_types import (
    RawTranslationCue,
    SourceCue,
    SourceWord,
    SubtitleCue,
    TranslationEngine,
    TranslationModelSelection,
    TranslationRequest,
    TranslationResult,
    TranslationRouteLeg,
    TranslationRoutePreview,
    TranslationServiceEvent,
    TranslationServiceState,
    TranslationSettings,
)


PIPELINE_REVISION = "translation-raw-layout-service-v3"
LAYOUT_REVISION = "subtitle-layout-rendered-lines-v2"
DEFAULT_TRANSLATION_MODEL = "hy-mt2-1.8b-q8"


class TranslationError(RuntimeError):
    pass


class TranslationConfigError(TranslationError):
    pass


class TranslationCancelled(TranslationError):
    pass


class TranslationStageError(TranslationError):
    def __init__(
        self,
        message: str,
        intermediate_cues: tuple[SubtitleCue, ...] = (),
    ):
        super().__init__(message)
        self.intermediate_cues = intermediate_cues


class TranslationValidationError(TranslationError):
    pass


def normalize_language(language: str | None) -> str | None:
    if language is None:
        return None
    from .model_manager import normalize_language as normalize_catalog_language
    return normalize_catalog_language(language)


def normalize_open_language(language: str | None) -> str | None:
    if language is None:
        return None
    value = language.strip().lower().replace("_", "-")
    aliases = {
        "english": "en",
        "italian": "it",
        "french": "fr",
        "spanish": "es",
        "german": "de",
        "portuguese": "pt",
        "japanese": "ja",
        "korean": "ko",
        "chinese": "zh-cn",
        "mandarin": "zh-cn",
        "turkish": "tr",
    }
    return aliases.get(value, value) or None


def normalize_detected_language(language: str | None) -> str:
    if language is None:
        return "unknown"
    value = language.strip().lower().replace("_", "-")
    aliases = {
        "english": "en",
        "italian": "it",
        "french": "fr",
        "spanish": "es",
        "german": "de",
        "portuguese": "pt",
    }
    return aliases.get(value, value) or "unknown"


def normalize_language_for_engine(engine: str, language: str | None) -> str | None:
    if engine == "local":
        return normalize_language(language)
    return normalize_open_language(language)


def is_english_language(language: str | None) -> bool:
    return normalize_open_language(language) == "en"


def default_cache_dir() -> str:
    from .model_manager import cache_root
    return str(cache_root())


def clear_translation_cache(cache_dir: str | None = None) -> None:
    root = Path(cache_dir or default_cache_dir()).resolve()
    for stage in ("source", "translation", "layout"):
        stage_dir = (root / stage).resolve()
        if not stage_dir.is_relative_to(root):
            raise TranslationConfigError(f"Refusing to clear cache outside {root}")
        if stage_dir.is_dir():
            shutil.rmtree(stage_dir)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_digest(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def source_cache_key(input_path: str, settings: dict[str, Any]) -> str:
    return stable_json_digest(
        {
            "revision": PIPELINE_REVISION,
            "stage": "source",
            "input_sha256": file_sha256(input_path),
            "settings": settings,
        }
    )


def translation_cache_key(
    source_key: str,
    source_language: str,
    target_language: str,
    route: str,
    engine: str,
    model_id: str | None,
    model_revision: str | None,
    settings: TranslationSettings,
    stage: str,
    context: str = "",
    glossary: dict[str, str] | None = None,
    cue_digest: str | None = None,
    include_layout: bool = True,
    unit_mode: str = "adaptive",
) -> str:
    payload = {
        "revision": PIPELINE_REVISION,
        "settings_revision": settings.revision,
        "stage": stage,
        "source_key": source_key,
        "source_language": normalize_open_language(source_language),
        "target_language": normalize_open_language(target_language),
        "route": route,
        "engine": engine,
        "model_id": model_id,
        "model_revision": model_revision,
        "context": context,
        "glossary": glossary or {},
        "device": settings.device,
        "cue_digest": cue_digest,
        "unit_mode": unit_mode,
        "uniting": {
            "max_merge_gap": settings.max_merge_gap,
            "max_cues_per_unit": settings.max_cues_per_unit,
            "max_unit_duration": settings.max_unit_duration,
        },
    }
    if include_layout:
        payload["layout"] = {
            "adaptive_layout": settings.adaptive_layout,
            "bilingual": settings.bilingual,
            "max_chars_per_line": settings.max_chars_per_line,
            "max_lines": settings.max_lines,
            "max_cps": settings.max_cps,
            "min_duration": settings.min_duration,
            "max_duration": settings.max_duration,
        }
    return stable_json_digest(payload)


def raw_translation_cache_key(
    source_key: str,
    source_language: str,
    target_language: str,
    route: str,
    engine: str,
    model_id: str | None,
    model_revision: str | None,
    settings: TranslationSettings,
    stage: str,
    context: str = "",
    glossary: dict[str, str] | None = None,
    cue_digest: str | None = None,
    unit_mode: str = "adaptive",
) -> str:
    return translation_cache_key(
        source_key,
        source_language,
        target_language,
        route,
        engine,
        model_id,
        model_revision,
        settings,
        stage,
        context,
        glossary,
        cue_digest,
        include_layout=False,
        unit_mode=unit_mode,
    )


def layout_cache_key(
    source_key: str,
    stage: str,
    language: str,
    settings: TranslationSettings,
    raw_cues: tuple[RawTranslationCue, ...],
    bilingual: bool,
) -> str:
    return stable_json_digest(
        {
            "revision": PIPELINE_REVISION,
            "layout_revision": LAYOUT_REVISION,
            "settings_revision": settings.revision,
            "stage": stage,
            "source_key": source_key,
            "language": normalize_open_language(language),
            "raw_digest": raw_cue_digest(raw_cues),
            "layout": {
                "adaptive_layout": settings.adaptive_layout,
                "bilingual": bilingual,
                "max_chars_per_line": settings.max_chars_per_line,
                "max_lines": settings.max_lines,
                "max_cps": settings.max_cps,
                "min_duration": settings.min_duration,
                "max_duration": settings.max_duration,
                "max_merge_gap": settings.max_merge_gap,
            },
        }
    )


def raw_unit_mode(settings: TranslationSettings) -> str:
    if settings.bilingual or not settings.adaptive_layout:
        return "source-boundary-layout"
    return "adaptive-layout"


def cache_path(cache_dir: str | None, stage: str, key: str) -> str:
    return os.path.join(cache_dir or default_cache_dir(), stage, f"{key}.json")


def read_json_cache(cache_dir: str | None, stage: str, key: str) -> dict[str, Any] | None:
    path = cache_path(cache_dir, stage, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        quarantine_cache_file(path)
        return None


def quarantine_cache_file(path: str) -> None:
    try:
        os.replace(path, f"{path}.corrupt")
    except OSError:
        pass


def write_json_cache(cache_dir: str | None, stage: str, key: str, payload: dict[str, Any]) -> None:
    path = cache_path(cache_dir, stage, key)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def cache_payload_is_current(payload: dict[str, Any]) -> bool:
    return payload.get("revision") == PIPELINE_REVISION


def atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=directory or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def source_cues_from_transcript(
    transcript: Any,
    source_key: str,
    language: str | None,
) -> tuple[SourceCue, ...]:
    detected_language = normalize_detected_language(language or transcript_language(transcript))

    cues = []
    for index, segment in enumerate(normalize_segments(transcript), start=1):
        text = str(get_segment_value(segment, "text")).strip().replace("-->", "->")
        start = float(get_segment_value(segment, "start"))
        end = float(get_segment_value(segment, "end"))
        cue_id = make_source_cue_id(source_key, index, start, end, text)
        words = tuple(source_words_from_segment(segment, cue_id))
        metadata = {}
        for key in ("temperature", "avg_logprob", "compression_ratio", "no_speech_prob"):
            value = get_optional_segment_value(segment, key)
            if value is not None:
                metadata[key] = value
        cues.append(
            SourceCue(
                id=cue_id,
                index=index,
                start=start,
                end=end,
                text=text,
                language=detected_language,
                speaker=get_optional_segment_value(segment, "speaker"),
                words=words,
                metadata=metadata,
            )
        )
    return tuple(cues)


def transcript_language(transcript: Any) -> str | None:
    if isinstance(transcript, dict):
        return transcript.get("language") or transcript.get("detected_language")
    return getattr(transcript, "language", None) or getattr(transcript, "detected_language", None)


def make_source_cue_id(source_key: str, index: int, start: float, end: float, text: str) -> str:
    digest = stable_json_digest(
        {
            "source_key": source_key,
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
    )
    return f"src-{digest[:16]}"


def get_optional_segment_value(segment: Any, key: str) -> Any:
    if isinstance(segment, dict):
        return segment.get(key)
    return getattr(segment, key, None)


def source_words_from_segment(segment: Any, cue_id: str) -> list[SourceWord]:
    words = get_optional_segment_value(segment, "words") or []
    normalized = []
    for index, word in enumerate(words, start=1):
        text = get_optional_segment_value(word, "word")
        if text is None:
            text = get_optional_segment_value(word, "text")
        if text is None:
            continue
        start = get_optional_segment_value(word, "start")
        end = get_optional_segment_value(word, "end")
        if start is None or end is None:
            continue
        normalized.append(
            SourceWord(
                id=f"{cue_id}-w{index}",
                start=float(start),
                end=float(end),
                text=str(text),
            )
        )
    return normalized


def cache_source_transcript(
    cache_dir: str | None,
    key: str,
    cues: tuple[SourceCue, ...],
    language: str,
) -> None:
    write_json_cache(
        cache_dir,
        "source",
        key,
        {
            "revision": PIPELINE_REVISION,
            "language": language,
            "cues": [source_cue_to_dict(cue) for cue in cues],
        },
    )


def read_cached_source_transcript(
    cache_dir: str | None,
    key: str,
) -> tuple[str, tuple[SourceCue, ...]] | None:
    try:
        payload = read_json_cache(cache_dir, "source", key)
        if payload is None:
            return None
        cues = tuple(source_cue_from_dict(item) for item in payload["cues"])
        return payload["language"], cues
    except (KeyError, TypeError, ValueError):
        quarantine_cache_file(cache_path(cache_dir, "source", key))
        return None


def source_cue_from_dict(item: dict[str, Any]) -> SourceCue:
    return SourceCue(
        id=item["id"],
        index=int(item["index"]),
        start=float(item["start"]),
        end=float(item["end"]),
        text=item["text"],
        language=item["language"],
        speaker=item.get("speaker"),
        words=tuple(SourceWord(**word) for word in item.get("words", [])),
        metadata=item.get("metadata", {}),
    )


def subtitle_cue_from_dict(item: dict[str, Any]) -> SubtitleCue:
    return SubtitleCue(
        id=item["id"],
        source_ids=tuple(item["source_ids"]),
        start=float(item["start"]),
        end=float(item["end"]),
        text=item["text"],
        language=item["language"],
        source_text=item.get("source_text"),
        speaker=item.get("speaker"),
        metadata=item.get("metadata", {}),
    )


def raw_translation_cue_from_dict(item: dict[str, Any]) -> RawTranslationCue:
    return RawTranslationCue(
        id=item["id"],
        source_ids=tuple(item["source_ids"]),
        start=float(item["start"]),
        end=float(item["end"]),
        text=item["text"],
        language=item["language"],
        source_text=item["source_text"],
        speaker=item.get("speaker"),
        metadata=item.get("metadata", {}),
    )


def source_cue_to_dict(cue: SourceCue) -> dict[str, Any]:
    return {
        "id": cue.id,
        "index": cue.index,
        "start": cue.start,
        "end": cue.end,
        "text": cue.text,
        "language": cue.language,
        "speaker": cue.speaker,
        "words": [
            {
                "id": word.id,
                "start": word.start,
                "end": word.end,
                "text": word.text,
            }
            for word in cue.words
        ],
        "metadata": thaw_json(cue.metadata),
    }


def raw_translation_cue_to_dict(cue: RawTranslationCue) -> dict[str, Any]:
    return {
        "id": cue.id,
        "source_ids": list(cue.source_ids),
        "start": cue.start,
        "end": cue.end,
        "text": cue.text,
        "language": cue.language,
        "source_text": cue.source_text,
        "speaker": cue.speaker,
        "metadata": thaw_json(cue.metadata),
    }


def subtitle_cue_to_dict(cue: SubtitleCue) -> dict[str, Any]:
    return {
        "id": cue.id,
        "source_ids": list(cue.source_ids),
        "start": cue.start,
        "end": cue.end,
        "text": cue.text,
        "language": cue.language,
        "source_text": cue.source_text,
        "speaker": cue.speaker,
        "metadata": thaw_json(cue.metadata),
    }


def source_as_subtitle_cues(cues: tuple[SourceCue, ...]) -> tuple[SubtitleCue, ...]:
    return tuple(
        SubtitleCue(
            id=cue.id,
            source_ids=(cue.id,),
            start=cue.start,
            end=cue.end,
            text=cue.text,
            language=cue.language,
            speaker=cue.speaker,
            metadata={"source_index": cue.index},
        )
        for cue in cues
    )


class TranslationPipeline:
    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir
        self._local_engines: dict[tuple[str, str, str], TranslationEngine] = {}

    def close(self) -> None:
        for engine in self._local_engines.values():
            engine.close()
        self._local_engines.clear()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        self._check_cancel(request)
        settings = request.settings
        validate_translation_settings(settings)
        raw_source_language = settings.source_language or infer_source_language(request.cues)
        if raw_source_language is None or raw_source_language == "unknown":
            raise TranslationConfigError("Cannot translate without a detected or explicit source language.")
        source_language = normalize_language_for_engine(settings.engine, raw_source_language)
        target_language = normalize_language_for_engine(settings.engine, settings.target_language)
        if target_language is None:
            raise TranslationConfigError("Cannot translate without a target language.")
        self._validate_route_before_download(
            settings,
            source_language,
            target_language,
            has_context=bool(request.context.strip()),
            has_glossary=bool(request.glossary),
        )

        self._progress(request, "translating", progress=0.0, message="Starting translation")
        model_id = self._effective_model_signature(settings, source_language, target_language)
        model_revision = self._effective_model_revision(settings, source_language, target_language)
        if settings.route == "direct":
            raw_final_cues, final_raw_cache_hit = self._translate_raw_stage(request, request.cues, source_language, target_language, "final")
            raw_intermediate_cues: tuple[RawTranslationCue, ...] = ()
            intermediate_raw_cache_hit = True
        else:
            raw_intermediate_cues, intermediate_raw_cache_hit = self._translate_raw_stage(request, request.cues, source_language, "en", "intermediate")
            synthetic_source = raw_translations_as_source(raw_intermediate_cues)
            try:
                raw_final_cues, final_raw_cache_hit = self._translate_raw_stage(request, synthetic_source, "en", target_language, "final")
            except Exception as exc:
                intermediate_cues = self._layout_stage(request, raw_intermediate_cues, "en", "intermediate", False)
                raise TranslationStageError(str(exc), intermediate_cues=intermediate_cues) from exc

        final_cues = self._layout_stage(request, raw_final_cues, target_language, "final", settings.bilingual)
        intermediate_cues = (
            self._layout_stage(request, raw_intermediate_cues, "en", "intermediate", False)
            if raw_intermediate_cues
            else ()
        )
        warnings = collect_translation_warnings(raw_final_cues, raw_intermediate_cues, final_cues, intermediate_cues)
        self._progress(request, "completed", progress=1.0, message="Translation complete")
        return TranslationResult(
            source_key=request.source_key,
            source_language=source_language,
            target_language=target_language,
            route=settings.route,
            final_cues=final_cues,
            source_cues=request.cues,
            intermediate_cues=intermediate_cues,
            raw_final_cues=raw_final_cues,
            raw_intermediate_cues=raw_intermediate_cues,
            cache_hit=final_raw_cache_hit and intermediate_raw_cache_hit,
            model_id=model_id,
            model_revision=model_revision,
            warnings=warnings,
        )

    def _translate_raw_stage(
        self,
        request: TranslationRequest,
        cues: tuple[SourceCue, ...],
        source_language: str,
        target_language: str,
        stage: str,
    ) -> tuple[tuple[RawTranslationCue, ...], bool]:
        settings = request.settings
        stage_key = raw_translation_cache_key(
            request.source_key,
            source_language,
            target_language,
            settings.route,
            settings.engine,
            self._effective_leg_model_id(settings, source_language, target_language),
            self._effective_leg_model_revision(settings, source_language, target_language),
            settings,
            f"{stage}-raw",
            request.context,
            dict(request.glossary or {}),
            cue_digest(cues),
            unit_mode=raw_unit_mode(settings),
        )
        cached = read_json_cache(request.cache_dir or self.cache_dir, "translation", stage_key)
        if cached is not None and cache_payload_is_current(cached):
            items = cached.get("raw_cues", cached.get("cues", []))
            self._progress(
                request,
                "translating",
                message=f"Using cached {stage} translation",
                stage=stage,
                source_language=source_language,
                target_language=target_language,
                cached=True,
            )
            return tuple(raw_translation_cue_from_dict(item) for item in items), True

        engine = self._engine(settings, source_language, target_language, request)
        if (request.context.strip() or request.glossary) and not engine.contextual:
            raise TranslationConfigError("Context or glossary translation requires a contextual local translation model.")
        translated = []
        units = translation_units(cues, settings, engine.max_input_tokens, contextual=engine.contextual)
        context_windows = automatic_context_windows(cues)
        total = max(1, len(units))
        for unit_index, unit in enumerate(units, start=1):
            self._check_cancel(request)
            translated.extend(
                self._translate_raw_unit(
                    engine,
                    unit,
                    source_language,
                    target_language,
                    request,
                    settings,
                    context_windows,
                    stage,
                )
            )
            self._progress(
                request,
                "translating",
                progress=unit_index / total,
                message=f"Translated {unit_index} of {total} unit(s)",
                stage=stage,
                source_language=source_language,
                target_language=target_language,
                unit=unit_index,
                units=total,
            )

        cues_out = tuple(translated)
        write_json_cache(
            request.cache_dir or self.cache_dir,
            "translation",
            stage_key,
            {
                "revision": PIPELINE_REVISION,
                "raw_cues": [raw_translation_cue_to_dict(cue) for cue in cues_out],
            },
        )
        return cues_out, False

    def _translate_raw_unit(
        self,
        engine: TranslationEngine,
        unit: list[SourceCue],
        source_language: str,
        target_language: str,
        request: TranslationRequest,
        settings: TranslationSettings,
        context_windows: dict[str, tuple[SourceCue, ...]],
        stage: str,
        retry_malformed: bool = True,
    ) -> list[RawTranslationCue]:
        try:
            context = automatic_context_for_unit(unit, context_windows, request.context) if engine.contextual else ""
            translated_texts = engine.translate(
                [cue.text for cue in unit],
                source_language=source_language,
                target_language=target_language,
                context=context,
                glossary=dict(request.glossary or {}),
            )
        except ValueError as error:
            retry_for_malformed = retry_malformed and is_retryable_malformed_error(error)
            if "token" not in str(error).lower() and not retry_for_malformed:
                raise
            return self._retry_smaller_raw_unit(
                engine,
                unit,
                source_language,
                target_language,
                request,
                settings,
                context_windows,
                stage,
                retry_malformed=False if retry_for_malformed else retry_malformed,
                fallback_error=error,
                retry_same_unsplittable=retry_for_malformed,
            )
        if len(translated_texts) != len(unit):
            error = TranslationValidationError("Translation engine returned a different number of texts than requested.")
            if retry_malformed:
                return self._retry_smaller_raw_unit(
                    engine,
                    unit,
                    source_language,
                    target_language,
                    request,
                    settings,
                    context_windows,
                    stage,
                    retry_malformed=False,
                    fallback_error=error,
                    retry_same_unsplittable=True,
                )
            raise error
        raw_cues = []
        for cue, text in zip(unit, translated_texts):
            try:
                validation_warnings = validate_translated_text(
                    cue.text,
                    text,
                    source_language,
                    target_language,
                    dict(request.glossary or {}),
                    explicit_names_for_cue(cue),
                )
            except TranslationValidationError as error:
                if retry_malformed:
                    return self._retry_smaller_raw_unit(
                        engine,
                        unit,
                        source_language,
                        target_language,
                        request,
                        settings,
                        context_windows,
                        stage,
                        retry_malformed=False,
                        fallback_error=error,
                        retry_same_unsplittable=True,
                    )
                raise
            raw_cues.append(
                raw_translation_from_source(
                    cue,
                    text,
                    target_language,
                    stage,
                    self._effective_leg_model_revision(settings, source_language, target_language),
                    validation_warnings,
                )
            )
        return raw_cues

    def _retry_smaller_raw_unit(
        self,
        engine: TranslationEngine,
        unit: list[SourceCue],
        source_language: str,
        target_language: str,
        request: TranslationRequest,
        settings: TranslationSettings,
        context_windows: dict[str, tuple[SourceCue, ...]],
        stage: str,
        retry_malformed: bool,
        fallback_error: Exception,
        retry_same_unsplittable: bool,
    ) -> list[RawTranslationCue]:
        if len(unit) == 1:
            split = split_source_cue_for_translation(unit[0])
            if split is None:
                if retry_same_unsplittable:
                    return self._translate_raw_unit(
                        engine,
                        unit,
                        source_language,
                        target_language,
                        request,
                        settings,
                        context_windows,
                        stage,
                        retry_malformed=False,
                    )
                raise fallback_error
            smaller_units = [split]
        else:
            smaller_units = [[cue] for cue in unit]
        translated: list[RawTranslationCue] = []
        for smaller in smaller_units:
            translated.extend(
                self._translate_raw_unit(
                    engine,
                    smaller,
                    source_language,
                    target_language,
                    request,
                    settings,
                    context_windows,
                    stage,
                    retry_malformed=retry_malformed,
                )
            )
        return translated

    def _layout_stage(
        self,
        request: TranslationRequest,
        raw_cues: tuple[RawTranslationCue, ...],
        language: str,
        stage: str,
        bilingual: bool,
    ) -> tuple[SubtitleCue, ...]:
        key = layout_cache_key(request.source_key, f"{stage}-layout", language, request.settings, raw_cues, bilingual)
        cached = read_json_cache(request.cache_dir or self.cache_dir, "layout", key)
        if cached is not None and cache_payload_is_current(cached):
            return tuple(subtitle_cue_from_dict(item) for item in cached["cues"])
        cues = layout_raw_translation_cues(raw_cues, language, request.settings, bilingual)
        write_json_cache(
            request.cache_dir or self.cache_dir,
            "layout",
            key,
            {
                "revision": PIPELINE_REVISION,
                "layout_revision": LAYOUT_REVISION,
                "cues": [subtitle_cue_to_dict(cue) for cue in cues],
            },
        )
        return cues

    def _validate_route_before_download(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
        has_context: bool = False,
        has_glossary: bool = False,
    ) -> None:
        if settings.route == "via-en" and (is_english_language(source_language) or is_english_language(target_language)):
            raise TranslationConfigError("--translation-route via-en requires both source and target to be non-English.")
        if settings.engine == "google" and settings.offline:
            raise TranslationConfigError("--offline cannot be combined with --translation-engine google.")
        if settings.engine != "local":
            if has_glossary:
                raise TranslationConfigError("Glossary translation requires a contextual local translation model.")
            return
        if settings.route == "via-en":
            first = self._resolve_leg_model(settings, source_language, "en")
            second = self._resolve_leg_model(settings, "en", target_language)
            if first.family != second.family:
                raise TranslationConfigError(
                    f"--translation-route via-en requires the same model family for both legs; got {first.family} and {second.family}."
                )
            for spec in (first, second):
                if (has_context or has_glossary) and not spec.contextual:
                    raise TranslationConfigError("Context or glossary translation requires a contextual local translation model.")
            return
        spec = self._resolve_leg_model(settings, source_language, target_language)
        if (has_context or has_glossary) and not spec.contextual:
            raise TranslationConfigError("Context or glossary translation requires a contextual local translation model.")

    def _resolve_leg_model(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> Any:
        from .model_manager import resolve_model
        model_id = settings.model_id or read_default_translation_model()
        return resolve_model(model_id, source_language, target_language)

    def _engine(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
        request: TranslationRequest,
    ) -> TranslationEngine:
        if settings.engine == "google":
            return GoogleTranslationEngine()
        model_id = self._effective_leg_model_id(settings, source_language, target_language)
        engine_key = (model_id, source_language, target_language)
        existing = self._local_engines.get(engine_key)
        if existing is None or getattr(existing, "closed", False):
            self._local_engines[engine_key] = load_local_translation_engine(
                settings,
                source_language,
                target_language,
                request,
                model_id,
            )
        return self._local_engines[engine_key]

    def _effective_model_signature(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> str | None:
        if settings.engine != "local":
            return None
        model_id = settings.model_id or read_default_translation_model()
        if settings.route == "via-en":
            return "+".join(
                (
                    self._effective_leg_model_id(settings, source_language, "en"),
                    self._effective_leg_model_id(settings, "en", target_language),
                )
            )
        return self._effective_leg_model_id(settings, source_language, target_language)

    def _effective_leg_model_id(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> str | None:
        if settings.engine != "local":
            return None
        model_id = settings.model_id or read_default_translation_model()
        from .model_manager import resolve_model
        return resolve_model(model_id, source_language, target_language).id

    def _effective_leg_model_revision(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> str | None:
        if settings.engine == "google":
            return google_translation_revision()
        if settings.engine != "local":
            return None
        from .model_manager import resolve_model
        model_id = settings.model_id or read_default_translation_model()
        return resolve_model(model_id, source_language, target_language).revision

    def _effective_model_revision(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> str | None:
        if settings.engine == "google":
            if settings.route == "via-en":
                tag = google_translation_revision()
                return f"{tag}+{tag}"
            return google_translation_revision()
        if settings.engine != "local":
            return None
        from .model_manager import resolve_model
        model_id = settings.model_id or read_default_translation_model()
        if settings.route == "via-en":
            first = resolve_model(model_id, source_language, "en")
            second = resolve_model(model_id, "en", target_language)
            return f"{first.revision}+{second.revision}"
        return resolve_model(model_id, source_language, target_language).revision

    def _check_cancel(self, request: TranslationRequest) -> None:
        if request.cancel is None:
            return
        cancelled = request.cancel.is_set() if hasattr(request.cancel, "is_set") else request.cancel()
        if cancelled:
            raise shared_cancelled_error()

    def _progress(self, request: TranslationRequest, state: str, **details: Any) -> None:
        if request.progress is not None:
            request.progress({"state": state, **details})


class GoogleTranslationEngine:
    contextual = False
    max_input_tokens = 512

    def translate(
        self,
        texts: list[str],
        source_language: str,
        target_language: str,
        context: str = "",
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:
            raise TranslationConfigError(
                "Install deep-translator to use --translation-engine google."
            ) from exc
        translator = GoogleTranslator(source=source_language, target=target_language)
        return [str(text) for text in translator.translate_batch(texts)]

    def close(self) -> None:
        return None


class TranslationGuiService:
    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir
        self._pipeline = TranslationPipeline(cache_dir)
        self._queue_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._state = TranslationServiceState()

    @property
    def state(self) -> TranslationServiceState:
        return self._state

    def close(self) -> None:
        self.cancel()
        with self._queue_lock:
            self._pipeline.close()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._set_state("cancelled", cancellable=False)

    def clear_cache(self) -> None:
        with self._queue_lock:
            clear_translation_cache(self.cache_dir)
            self._set_state("idle", message="Translation cache cleared", cancellable=False)

    def retry(self, request: TranslationRequest, callback: Any | None = None) -> TranslationResult:
        return self.translate(request, callback)

    def translate(self, request: TranslationRequest, callback: Any | None = None) -> TranslationResult:
        with self._queue_lock:
            self._cancel_event.clear()
            queued_request = self._with_service_controls(request, callback)
            try:
                self._set_state("translating", progress=0.0, message="Queued translation started", cancellable=True)
                return self._pipeline.translate(queued_request)
            except BaseException as error:
                if is_cancelled_error(error):
                    self._set_state("cancelled", cancellable=False)
                    raise
                self._set_state("failed", cancellable=False)
                raise
            finally:
                self._cancel_event.clear()

    def route_preview(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str | None = None,
        has_context: bool = False,
        has_glossary: bool = False,
    ) -> TranslationRoutePreview:
        source = normalize_language_for_engine(settings.engine, source_language)
        target = normalize_language_for_engine(settings.engine, target_language or settings.target_language)
        if source is None or target is None:
            raise TranslationConfigError("Route preview requires source and target languages.")
        self._pipeline._validate_route_before_download(settings, source, target, has_context, has_glossary)
        legs = self._route_legs(settings, source, target)
        preview = TranslationRoutePreview(
            source_language=source,
            target_language=target,
            route=settings.route,
            engine=settings.engine,
            legs=legs,
            warnings=route_preview_warnings(legs),
        )
        self._set_state("route_preview", message="Route preview ready", details={"route": settings.route})
        return preview

    def model_selection(
        self,
        source_language: str | None = None,
        target_language: str | None = None,
        route: str = "direct",
        cache_dir: str | None = None,
    ) -> tuple[TranslationModelSelection, ...]:
        from .model_manager import list_models, model_metadata

        selections = [
            model_selection_from_spec(spec, model_metadata(spec, cache_dir or self.cache_dir))
            for spec in list_models(source_language, target_language, route=route)
        ]
        if route == "via-en":
            composite = opus_via_english_selection(source_language, target_language, cache_dir or self.cache_dir)
            if composite is not None:
                selections.append(composite)
        return tuple(selections)

    def _route_legs(
        self,
        settings: TranslationSettings,
        source_language: str,
        target_language: str,
    ) -> tuple[TranslationRouteLeg, ...]:
        if settings.engine != "local":
            pairs = ((source_language, "en"), ("en", target_language)) if settings.route == "via-en" else ((source_language, target_language),)
            return tuple(TranslationRouteLeg(source, target, "google") for source, target in pairs)
        pairs = ((source_language, "en"), ("en", target_language)) if settings.route == "via-en" else ((source_language, target_language),)
        legs = []
        for source, target in pairs:
            spec = self._pipeline._resolve_leg_model(settings, source, target)
            legs.append(
                TranslationRouteLeg(
                    source_language=source,
                    target_language=target,
                    engine="local",
                    model_id=spec.id,
                    model_revision=spec.revision,
                    family=spec.family,
                    contextual=spec.contextual,
                )
            )
        return tuple(legs)

    def _with_service_controls(
        self,
        request: TranslationRequest,
        callback: Any | None,
    ) -> TranslationRequest:
        def progress(event: dict[str, Any]) -> None:
            if request.progress is not None:
                request.progress(event)
            self._event(event, callback)

        def cancel() -> bool:
            upstream = request.cancel.is_set() if hasattr(request.cancel, "is_set") else request.cancel() if request.cancel else False
            return self._cancel_event.is_set() or bool(upstream)

        return TranslationRequest(
            source_key=request.source_key,
            cues=request.cues,
            settings=request.settings,
            context=request.context,
            glossary=request.glossary,
            cache_dir=request.cache_dir or self.cache_dir,
            progress=progress,
            cancel=cancel,
        )

    def _event(self, event: dict[str, Any], callback: Any | None) -> None:
        state = str(event.get("state", "translating"))
        message = str(event.get("message") or event.get("model") or "")
        progress = event.get("progress")
        details = {key: value for key, value in event.items() if key not in {"state", "message", "progress"}}
        self._set_state(state, progress=progress, message=message, cancellable=state not in {"completed", "failed", "cancelled"}, details=details)
        if callback is not None:
            callback(TranslationServiceEvent(state=state, message=message, progress=progress, details=details))

    def _set_state(
        self,
        state: str,
        progress: float | None = None,
        message: str = "",
        cancellable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(details or {})
        if message:
            payload["message"] = message
        self._state = TranslationServiceState(state=state, progress=progress, cancellable=cancellable, details=payload)


def model_selection_from_spec(spec: Any, info: dict[str, Any]) -> TranslationModelSelection:
    return TranslationModelSelection(
        id=spec.id,
        family=spec.family,
        revision=spec.revision,
        source=spec.source,
        target=spec.target,
        status=info["status"],
        download_size=int(info["download_size"]),
        context_mode=info["context_mode"],
        notice=info.get("notice", ""),
    )


def opus_via_english_selection(
    source_language: str | None,
    target_language: str | None,
    cache_dir: str | None,
) -> TranslationModelSelection | None:
    if not source_language or not target_language:
        return None
    source = normalize_language(source_language)
    target = normalize_language(target_language)
    if is_english_language(source) or is_english_language(target):
        return None
    try:
        from .model_manager import model_metadata, resolve_model

        first = resolve_model("opus-mt", source, "en")
        second = resolve_model("opus-mt", "en", target)
        first_info = model_metadata(first, cache_dir)
        second_info = model_metadata(second, cache_dir)
    except Exception:
        return None
    status = "installed" if first_info["status"] == second_info["status"] == "installed" else "not-installed"
    return TranslationModelSelection(
        id="opus-mt",
        family="opus",
        revision=f"{first.revision}+{second.revision}",
        source=source,
        target=target,
        status=status,
        download_size=int(first_info["download_size"]) + int(second_info["download_size"]),
        context_mode="sentence",
        notice=f"Composite via-English route: opus-{source}-en + opus-en-{target}",
    )


def route_preview_warnings(legs: tuple[TranslationRouteLeg, ...]) -> tuple[str, ...]:
    warnings = []
    if any(not leg.contextual for leg in legs):
        warnings.append("sentence_based_translation")
    if len({leg.family for leg in legs if leg.family}) > 1:
        warnings.append("mixed_model_families")
    return tuple(warnings)


def load_local_translation_engine(
    settings: TranslationSettings,
    source_language: str,
    target_language: str,
    request: TranslationRequest,
    model_id: str,
) -> TranslationEngine:
    from .local_translation import get_translation_engine

    return get_translation_engine(
        model_id,
        source_language,
        target_language,
        device=settings.device,
        offline=settings.offline,
        progress=request.progress,
        cancel=request.cancel,
        cache_dir=request.cache_dir,
    )


def shared_cancelled_error() -> Exception:
    from .model_manager import CancelledError
    return CancelledError("Translation cancelled")


def is_cancelled_error(error: BaseException) -> bool:
    try:
        from .model_manager import CancelledError
    except ImportError:
        cancelled_types = (TranslationCancelled,)
    else:
        cancelled_types = (TranslationCancelled, CancelledError)
    return isinstance(error, cancelled_types) or "cancel" in str(error).lower()


def read_default_translation_model() -> str:
    from .model_manager import DEFAULT_TRANSLATION_MODEL as configured_default
    return configured_default


def google_translation_revision() -> str:
    try:
        from importlib.metadata import version

        package_version = version("deep-translator")
    except Exception:
        package_version = "unknown"
    return f"google-deep-translator:{package_version}"


def infer_source_language(cues: tuple[SourceCue, ...]) -> str | None:
    for cue in cues:
        language = cue.language
        if language:
            return language
    return None


def translated_cues_as_source(
    cues: tuple[SubtitleCue, ...],
    original_cues: tuple[SourceCue, ...],
) -> tuple[SourceCue, ...]:
    originals = {cue.id: cue for cue in original_cues}
    synthetic = []
    for index, cue in enumerate(cues, start=1):
        source_id = cue.source_ids[0]
        original = originals.get(source_id)
        metadata = dict(cue.metadata)
        metadata["intermediate_id"] = cue.id
        synthetic.append(
            SourceCue(
                id=source_id,
                index=index,
                start=cue.start,
                end=cue.end,
                text=cue.text,
                language=cue.language,
                speaker=cue.speaker,
                metadata={
                    **metadata,
                    "original_text": original.text if original is not None else cue.source_text,
                },
            )
        )
    return tuple(synthetic)


def raw_translations_as_source(cues: tuple[RawTranslationCue, ...]) -> tuple[SourceCue, ...]:
    return tuple(
        SourceCue(
            id=cue.id,
            index=index,
            start=cue.start,
            end=cue.end,
            text=cue.text,
            language=cue.language,
            speaker=cue.speaker,
            metadata={
                "source_ids": cue.source_ids,
                "source_text": cue.source_text,
                "intermediate_id": cue.id,
                "timing_estimated": cue.metadata.get("timing_estimated", False),
            },
        )
        for index, cue in enumerate(cues, start=1)
    )


def translation_units(
    cues: tuple[SourceCue, ...],
    settings: TranslationSettings,
    max_input_tokens: int,
    contextual: bool = True,
) -> list[list[SourceCue]]:
    if not contextual:
        return [[cue] for cue in sentence_oriented_cues(cues, settings, max_input_tokens)]
    units: list[list[SourceCue]] = []
    current: list[SourceCue] = []
    current_tokens = 0
    for cue in cues:
        cue_tokens = estimate_tokens(cue.text)
        if current and should_start_new_unit(current, cue, current_tokens + cue_tokens, settings, max_input_tokens):
            units.append(current)
            current = []
            current_tokens = 0
        current.append(cue)
        current_tokens += cue_tokens
    if current:
        units.append(current)
    return units


def sentence_oriented_cues(
    cues: tuple[SourceCue, ...],
    settings: TranslationSettings,
    max_input_tokens: int,
) -> tuple[SourceCue, ...]:
    merged: list[SourceCue] = []
    current: list[SourceCue] = []
    current_tokens = 0
    for cue in cues:
        cue_tokens = estimate_tokens(cue.text)
        if current and (
            sentence_is_complete(" ".join(item.text for item in current))
            or should_start_new_unit(current, cue, current_tokens + cue_tokens, settings, max_input_tokens)
        ):
            merged.append(merge_source_cues_for_translation(current))
            current = []
            current_tokens = 0
        current.append(cue)
        current_tokens += cue_tokens
    if current:
        merged.append(merge_source_cues_for_translation(current))
    return tuple(merged)


def sentence_is_complete(text: str) -> bool:
    return text.rstrip().endswith((".", "!", "?", "。", "！", "？"))


def merge_source_cues_for_translation(cues: list[SourceCue]) -> SourceCue:
    if len(cues) == 1:
        return cues[0]
    source_ids = tuple(source_ids_for_cue(cue) for cue in cues)
    flat_source_ids = tuple(source_id for group in source_ids for source_id in group)
    text = normalize_subtitle_text(" ".join(cue.text for cue in cues))
    source_text = normalize_subtitle_text(
        " ".join(str(cue.metadata.get("source_text", cue.metadata.get("original_text", cue.text))) for cue in cues)
    )
    speaker = cues[0].speaker if all(cue.speaker == cues[0].speaker for cue in cues) else None
    words = tuple(word for cue in cues for word in cue.words)
    return SourceCue(
        id="merge-" + stable_json_digest({"source_ids": flat_source_ids, "text": text})[:16],
        index=cues[0].index,
        start=cues[0].start,
        end=cues[-1].end,
        text=text,
        language=cues[0].language,
        speaker=speaker,
        words=words,
        metadata={
            "source_ids": flat_source_ids,
            "source_text": source_text,
            "source_cue_anchors": [
                {
                    "id": source_id,
                    "start": cue.start,
                    "end": cue.end,
                    "text": cue.text,
                }
                for cue in cues
                for source_id in source_ids_for_cue(cue)
            ],
            "timing_estimated": True,
            "merged_for_sentence_translation": True,
        },
    )


def should_start_new_unit(
    current: list[SourceCue],
    cue: SourceCue,
    next_tokens: int,
    settings: TranslationSettings,
    max_input_tokens: int,
) -> bool:
    previous = current[-1]
    gap = cue.start - previous.end
    speaker_changed = previous.speaker is not None and cue.speaker is not None and previous.speaker != cue.speaker
    overlaps = cue.start < previous.end
    duration = cue.end - current[0].start
    return (
        len(current) >= settings.max_cues_per_unit
        or duration > settings.max_unit_duration
        or next_tokens > max_input_tokens
        or gap > settings.max_merge_gap
        or speaker_changed
        or overlaps
    )


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def validate_translation_settings(settings: TranslationSettings) -> None:
    numeric_values = (
        settings.max_chars_per_line,
        settings.max_lines,
        settings.max_cps,
        settings.min_duration,
        settings.max_duration,
        settings.max_merge_gap,
        settings.max_cues_per_unit,
        settings.max_unit_duration,
    )
    if not all(math.isfinite(float(value)) for value in numeric_values):
        raise TranslationConfigError("Translation layout and unit settings must be finite.")
    if settings.max_chars_per_line <= 0:
        raise TranslationConfigError("max_chars_per_line must be positive.")
    if settings.max_lines <= 0:
        raise TranslationConfigError("max_lines must be positive.")
    if settings.max_cps <= 0:
        raise TranslationConfigError("max_cps must be positive.")
    if settings.min_duration <= 0 or settings.max_duration <= 0:
        raise TranslationConfigError("subtitle durations must be positive.")
    if settings.min_duration > settings.max_duration:
        raise TranslationConfigError("min_duration cannot exceed max_duration.")
    if settings.max_cues_per_unit <= 0:
        raise TranslationConfigError("max_cues_per_unit must be positive.")
    if settings.max_unit_duration <= 0:
        raise TranslationConfigError("max_unit_duration must be positive.")
    if settings.max_merge_gap < 0:
        raise TranslationConfigError("max_merge_gap cannot be negative.")
    if settings.max_cues_per_unit > 6:
        raise TranslationConfigError("max_cues_per_unit cannot exceed 6.")
    if settings.max_unit_duration > 20.0:
        raise TranslationConfigError("max_unit_duration cannot exceed 20 seconds.")
    if settings.max_merge_gap > 0.25:
        raise TranslationConfigError("max_merge_gap cannot exceed 0.25 seconds.")


def layout_translated_cue(
    cue: SourceCue,
    translated_text: str,
    language: str,
    settings: TranslationSettings,
) -> tuple[SubtitleCue, ...]:
    raw = raw_translation_from_source(cue, translated_text, language, "final", None, ())
    return layout_raw_translation_cues((raw,), language, settings, settings.bilingual)


def raw_translation_from_source(
    cue: SourceCue,
    translated_text: str,
    language: str,
    stage: str,
    model_revision: str | None,
    validation_warnings: tuple[str, ...],
) -> RawTranslationCue:
    clean_text = normalize_subtitle_text(translated_text)
    source_ids = source_ids_for_cue(cue)
    estimated = bool(cue.metadata.get("timing_estimated") or cue.metadata.get("split_part") or len(source_ids) != 1)
    metadata = {
        "stage": stage,
        "pipeline_revision": PIPELINE_REVISION,
        "model_revision": model_revision,
        "exact_timing": not estimated,
        "timing_estimated": estimated,
        "source_cue_anchors": source_cue_anchors_for(cue),
        "source_word_anchors": source_word_anchors_for(cue),
    }
    if validation_warnings:
        metadata["validation_warnings"] = validation_warnings
    if cue.words:
        metadata["timing_anchor_count"] = len(cue.words)
    if cue.metadata.get("split_part"):
        metadata["split_part"] = cue.metadata["split_part"]
        metadata["split_count"] = cue.metadata.get("split_count", 2)
    if cue.metadata.get("merged_for_sentence_translation"):
        metadata["merged_for_sentence_translation"] = True
    return RawTranslationCue(
        id=f"{stage}-raw-{stable_json_digest({'source_ids': source_ids, 'text': clean_text, 'span': [cue.start, cue.end]})[:16]}",
        source_ids=source_ids,
        start=cue.start,
        end=cue.end,
        text=clean_text,
        language=language,
        source_text=str(cue.metadata.get("source_text", cue.metadata.get("original_text", cue.text))),
        speaker=cue.speaker,
        metadata=metadata,
    )


def source_ids_for_cue(cue: SourceCue) -> tuple[str, ...]:
    value = cue.metadata.get("source_ids")
    if value:
        return tuple(str(item) for item in value)
    return (cue.id,)


def source_cue_anchors_for(cue: SourceCue) -> tuple[dict[str, Any], ...]:
    value = cue.metadata.get("source_cue_anchors")
    if value:
        return tuple(
            {
                "id": str(item["id"]),
                "start": float(item["start"]),
                "end": float(item["end"]),
                "text": str(item.get("text", "")),
            }
            for item in value
            if "id" in item and "start" in item and "end" in item
        )
        return tuple(
            {
                "id": source_id,
                "start": cue.start,
                "end": cue.end,
                "text": cue.text,
            }
            for source_id in source_ids_for_cue(cue)
        )


def source_word_anchors_for(cue: SourceCue) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "id": word.id,
            "start": word.start,
            "end": word.end,
            "text": word.text,
        }
        for word in cue.words
    )


def layout_raw_translation_cues(
    raw_cues: tuple[RawTranslationCue, ...],
    language: str,
    settings: TranslationSettings,
    bilingual: bool,
) -> tuple[SubtitleCue, ...]:
    if not settings.adaptive_layout or bilingual:
        preserved = rejoin_split_raw_cues(reproject_raw_cues_to_source_boundaries(raw_cues))
        return tuple(layout_preserved_raw_cue(cue, language, bilingual) for cue in preserved)
    output: list[SubtitleCue] = []
    for span_index, span in enumerate(raw_speech_spans(raw_cues, settings), start=1):
        output.extend(layout_raw_span(span, language, settings, bilingual, span_index))
    return tuple(output)


def layout_preserved_raw_cue(cue: RawTranslationCue, language: str, bilingual: bool) -> SubtitleCue:
    metadata = {
        **dict(cue.metadata),
        "layout_stage": "preserve",
        "layout_index": 1,
        "layout_count": 1,
    }
    return SubtitleCue(
        id=f"{cue.id}-layout",
        source_ids=cue.source_ids,
        start=cue.start,
        end=cue.end,
        text=cue.text,
        language=language,
        source_text=cue.source_text if bilingual else None,
        speaker=cue.speaker,
        metadata=metadata,
    )


def reproject_raw_cues_to_source_boundaries(raw_cues: tuple[RawTranslationCue, ...]) -> tuple[RawTranslationCue, ...]:
    output: list[RawTranslationCue] = []
    for cue in raw_cues:
        anchors = tuple(cue.metadata.get("source_cue_anchors") or ())
        if len(anchors) <= 1 or len(cue.source_ids) <= 1:
            output.append(cue)
            continue
        anchor_text_groups = split_text_by_anchor_groups(cue.text, anchors)
        for index, (anchor_group, text) in enumerate(anchor_text_groups, start=1):
            first = anchor_group[0]
            last = anchor_group[-1]
            text_allocation_estimated = len(anchor_group) != 1 or len(anchor_text_groups) != len(anchors)
            warnings = list(cue.metadata.get("warnings") or ())
            if text_allocation_estimated:
                warnings.append("text_allocation_estimated")
            metadata = {
                **dict(cue.metadata),
                "exact_timing": True,
                "timing_estimated": False,
                "reprojected_to_source_boundary": True,
                "text_allocation_estimated": text_allocation_estimated,
                "reprojected_index": index,
                "reprojected_count": len(anchor_text_groups),
                "source_cue_anchors": tuple(anchor_group),
            }
            if warnings:
                metadata["warnings"] = tuple(dict.fromkeys(str(warning) for warning in warnings))
            output.append(
                RawTranslationCue(
                    id=f"{cue.id}-src-{index}",
                    source_ids=tuple(str(anchor.get("id")) for anchor in anchor_group),
                    start=float(first.get("start")),
                    end=float(last.get("end")),
                    text=text,
                    language=cue.language,
                    source_text=normalize_subtitle_text(" ".join(str(anchor.get("text") or "") for anchor in anchor_group)) or cue.source_text,
                    speaker=cue.speaker,
                    metadata=metadata,
                )
            )
    return tuple(output)


def split_text_by_anchor_weights(text: str, anchors: tuple[Any, ...]) -> list[str]:
    return [chunk for _anchors, chunk in split_text_by_anchor_groups(text, anchors)]


def split_text_by_anchor_groups(text: str, anchors: tuple[Any, ...]) -> list[tuple[tuple[Any, ...], str]]:
    tokens = layout_tokens(text)
    if len(anchors) <= 1 or not tokens:
        return [(anchors, text)]
    if len(tokens) < len(anchors):
        groups = grouped_anchors_for_short_translation(anchors, len(tokens))
        return [(group, tokens[index]) for index, group in enumerate(groups)]
    weights = [max(1, len(layout_tokens(str(anchor.get("text", ""))))) for anchor in anchors]
    total = sum(weights)
    chunks: list[list[str]] = []
    cursor = 0
    for index, weight in enumerate(weights, start=1):
        if index == len(weights):
            next_cursor = len(tokens)
        else:
            next_cursor = max(cursor + 1, round(len(tokens) * sum(weights[:index]) / total))
            remaining = len(weights) - index
            next_cursor = min(next_cursor, len(tokens) - remaining)
        chunks.append(tokens[cursor:next_cursor])
        cursor = next_cursor
    return [
        ((anchor,), normalize_subtitle_text(" ".join(chunk)))
        for anchor, chunk in zip(anchors, chunks)
    ]


def grouped_anchors_for_short_translation(anchors: tuple[Any, ...], group_count: int) -> list[tuple[Any, ...]]:
    group_count = max(1, min(group_count, len(anchors)))
    groups: list[tuple[Any, ...]] = []
    cursor = 0
    for index in range(group_count):
        remaining_groups = group_count - index - 1
        next_cursor = round((index + 1) * len(anchors) / group_count)
        next_cursor = max(cursor + 1, min(next_cursor, len(anchors) - remaining_groups))
        groups.append(tuple(anchors[cursor:next_cursor]))
        cursor = next_cursor
    return groups


def rejoin_split_raw_cues(raw_cues: tuple[RawTranslationCue, ...]) -> tuple[RawTranslationCue, ...]:
    output: list[RawTranslationCue] = []
    index = 0
    while index < len(raw_cues):
        cue = raw_cues[index]
        group = [cue]
        index += 1
        while index < len(raw_cues) and can_rejoin_raw_cues(group[-1], raw_cues[index]):
            group.append(raw_cues[index])
            index += 1
        output.append(rejoin_raw_group(group) if len(group) > 1 else cue)
    return tuple(output)


def can_rejoin_raw_cues(left: RawTranslationCue, right: RawTranslationCue) -> bool:
    return (
        left.source_ids == right.source_ids
        and left.language == right.language
        and left.speaker == right.speaker
        and bool(left.metadata.get("split_part") or right.metadata.get("split_part"))
    )


def rejoin_raw_group(group: list[RawTranslationCue]) -> RawTranslationCue:
    first = group[0]
    anchors = tuple(group[0].metadata.get("source_cue_anchors") or ())
    start = min((float(item.get("start")) for item in anchors), default=first.start)
    end = max((float(item.get("end")) for item in anchors), default=group[-1].end)
    metadata = {
        **dict(first.metadata),
        "exact_timing": True,
        "timing_estimated": False,
        "translation_split_rejoined": True,
        "source_word_anchors": tuple(item for cue in group for item in (cue.metadata.get("source_word_anchors") or ())),
    }
    return RawTranslationCue(
        id=f"{first.id}-rejoined",
        source_ids=first.source_ids,
        start=start,
        end=end,
        text=normalize_subtitle_text(" ".join(cue.text for cue in group)),
        language=first.language,
        source_text=first.source_text,
        speaker=first.speaker,
        metadata=metadata,
    )


def raw_speech_spans(
    raw_cues: tuple[RawTranslationCue, ...],
    settings: TranslationSettings,
) -> list[list[RawTranslationCue]]:
    spans: list[list[RawTranslationCue]] = []
    current: list[RawTranslationCue] = []
    for cue in raw_cues:
        if current and raw_span_boundary(current, cue, settings):
            spans.append(current)
            current = []
        current.append(cue)
    if current:
        spans.append(current)
    return spans


def raw_span_boundary(
    current: list[RawTranslationCue],
    cue: RawTranslationCue,
    settings: TranslationSettings,
) -> bool:
    previous = current[-1]
    gap = cue.start - previous.end
    speaker_changed = previous.speaker is not None and cue.speaker is not None and previous.speaker != cue.speaker
    return (
        len(current) >= settings.max_cues_per_unit
        or cue.end - current[0].start > settings.max_unit_duration
        or gap > settings.max_merge_gap
        or cue.start < previous.end
        or speaker_changed
    )


def layout_raw_span(
    span: list[RawTranslationCue],
    language: str,
    settings: TranslationSettings,
    bilingual: bool,
    span_index: int,
) -> list[SubtitleCue]:
    start = span[0].start
    end = span[-1].end
    text = normalize_subtitle_text(" ".join(cue.text for cue in span))
    source_text = normalize_subtitle_text(" ".join(cue.source_text for cue in span))
    source_ids = tuple(source_id for cue in span for source_id in cue.source_ids)
    chunks = [wrap_chunk_for_subtitle(chunk, settings) for chunk in split_text_for_layout(text, end - start, settings)]
    spans = anchored_spans(start, end, chunks, span, settings)
    warnings = layout_warnings(chunks, spans, settings)
    timing_estimated = len(span) != 1 or len(chunks) != 1 or any(cue.metadata.get("timing_estimated") for cue in span)
    speaker = span[0].speaker if all(cue.speaker == span[0].speaker for cue in span) else None
    return [
        SubtitleCue(
            id=f"{span[0].id}-layout-{span_index}-{index}",
            source_ids=source_ids,
            start=chunk_start,
            end=chunk_end,
            text=chunk,
            language=language,
            source_text=source_text if bilingual else None,
            speaker=speaker,
            metadata={
                "stage": span[0].metadata.get("stage"),
                "pipeline_revision": PIPELINE_REVISION,
                "model_revision": span[0].metadata.get("model_revision"),
                "layout_stage": "adaptive",
                "layout_index": index,
                "layout_count": len(chunks),
                "exact_timing": not timing_estimated,
                "timing_estimated": timing_estimated,
                "warnings": warnings,
            },
        )
        for index, (chunk, (chunk_start, chunk_end)) in enumerate(zip(chunks, spans), start=1)
    ]


def layout_warnings(
    chunks: list[str],
    spans: list[tuple[float, float]],
    settings: TranslationSettings,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for chunk, (start, end) in zip(chunks, spans):
        duration = max(0.0, end - start)
        visible_length = len(chunk.replace("\n", ""))
        lines = chunk.splitlines() or [chunk]
        if len(lines) > settings.max_lines or any(len(line) > settings.max_chars_per_line for line in lines):
            warnings.append("layout_impossible_chars")
        if duration < settings.min_duration:
            warnings.append("layout_impossible_min_duration")
        if duration > settings.max_duration:
            warnings.append("layout_impossible_max_duration")
        if duration > 0 and visible_length / duration > settings.max_cps:
            warnings.append("layout_impossible_cps")
    return tuple(dict.fromkeys(warnings))


def anchored_spans(
    start: float,
    end: float,
    chunks: list[str],
    raw_span: list[RawTranslationCue],
    settings: TranslationSettings,
) -> list[tuple[float, float]]:
    if len(chunks) == 1:
        return [(start, end)]
    proportional = proportional_spans(start, end, chunks)
    candidates = internal_timing_anchors(raw_span, start, end)
    if not candidates:
        return proportional
    boundaries: list[float] = []
    previous = start
    for index, (_chunk_start, target) in enumerate(proportional[:-1], start=1):
        remaining = len(chunks) - index
        low = previous + settings.min_duration
        high = end - settings.min_duration * remaining
        if low > high:
            boundaries.append(target)
            previous = target
            continue
        nearest = nearest_anchor(target, candidates, low, high)
        boundary = nearest if nearest is not None else target
        boundaries.append(boundary)
        previous = boundary
    cursor = start
    spans = []
    for boundary in boundaries:
        spans.append((cursor, min(end, max(cursor, boundary))))
        cursor = boundary
    spans.append((cursor, end))
    return spans


def internal_timing_anchors(
    raw_span: list[RawTranslationCue],
    start: float,
    end: float,
) -> tuple[float, ...]:
    anchors: list[float] = []
    for cue in raw_span:
        for item in cue.metadata.get("source_cue_anchors") or ():
            add_internal_anchor(anchors, item.get("start"), start, end)
            add_internal_anchor(anchors, item.get("end"), start, end)
        for item in cue.metadata.get("source_word_anchors") or ():
            add_internal_anchor(anchors, item.get("start"), start, end)
            add_internal_anchor(anchors, item.get("end"), start, end)
    return tuple(sorted(dict.fromkeys(round(anchor, 3) for anchor in anchors)))


def add_internal_anchor(anchors: list[float], value: Any, start: float, end: float) -> None:
    try:
        anchor = float(value)
    except (TypeError, ValueError):
        return
    if start < anchor < end:
        anchors.append(anchor)


def nearest_anchor(target: float, anchors: tuple[float, ...], low: float, high: float) -> float | None:
    valid = [anchor for anchor in anchors if low <= anchor <= high]
    if not valid:
        return None
    return min(valid, key=lambda anchor: abs(anchor - target))


def validate_translated_text(
    source_text: str,
    translated_text: str,
    source_language: str,
    target_language: str,
    glossary: dict[str, str],
    explicit_names: tuple[str, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    clean = normalize_subtitle_text(translated_text)
    if not clean:
        raise TranslationValidationError("Translation returned empty text.")
    if normalize_open_language(source_language) != normalize_open_language(target_language):
        source_clean = normalize_subtitle_text(source_text)
        if looks_like_untranslated_fallback(source_clean, clean):
            raise TranslationValidationError("Translation returned untranslated source text.")
    for token in protected_markup(source_text):
        if token not in translated_text:
            raise TranslationValidationError(f"Translation did not preserve markup token {token!r}.")
    source_numbers, source_number_warnings = normalized_number_multiset(source_text, source_language)
    target_numbers, target_number_warnings = normalized_number_multiset(translated_text, target_language)
    warnings.extend(source_number_warnings)
    warnings.extend(target_number_warnings)
    if not source_number_warnings and not target_number_warnings and source_numbers != target_numbers:
        raise TranslationValidationError("Translation did not preserve numbers.")
    for name in explicit_names:
        if name not in translated_text:
            raise TranslationValidationError(f"Translation did not preserve name {name!r}.")
    for name in high_confidence_names(source_text) + uncertain_entity_warnings(source_text, source_language):
        if name not in translated_text:
            warnings.append(f"uncertain_entity_not_preserved:{name}")
    for source_term, target_term in glossary.items():
        if source_term and source_term in source_text and target_term and target_term not in translated_text:
            raise TranslationValidationError(f"Translation did not apply glossary term {source_term!r}.")
    return tuple(dict.fromkeys(warnings))


def protected_markup(text: str) -> tuple[str, ...]:
    patterns = (
        r"<[^>\s]+(?:\s+[^>]*)?>",
        r"</[^>\s]+>",
        r"\{\\[^}]+\}",
        r"\[[A-Za-z][A-Za-z0-9_-]*\]",
        r"\[/[A-Za-z][A-Za-z0-9_-]*\]",
    )
    items: list[str] = []
    for pattern in patterns:
        items.extend(re.findall(pattern, text))
    return tuple(dict.fromkeys(items))


def protected_numbers(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"(?<!\w)[+-]?\d+(?:(?:[.,:h])\d+)*(?!\w)", text))


def normalized_number_multiset(text: str, language: str) -> tuple[Counter[str], tuple[str, ...]]:
    values: list[str] = []
    warnings: list[str] = []
    for token in protected_numbers(text):
        value = normalize_number_token(token, language)
        if value is None:
            warnings.append(f"uncertain_number_format:{token}")
        else:
            values.append(value)
    return Counter(values), tuple(dict.fromkeys(warnings))


def normalize_number_token(token: str, language: str) -> str | None:
    value = token.strip()
    time_match = re.fullmatch(r"(\d{1,2})[:h](\d{2})", value, flags=re.IGNORECASE)
    if time_match:
        hours = int(time_match.group(1))
        minutes = int(time_match.group(2))
        if minutes < 60:
            return f"time:{hours}:{minutes:02d}"
        return None
    sign = ""
    if value.startswith(("+", "-")):
        sign, value = value[0], value[1:]
    if not value:
        return None
    if "," in value and "." in value:
        return None
    separator = "," if "," in value else "." if "." in value else ""
    if not separator:
        return decimal_number_key(sign + value)
    parts = value.split(separator)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    before, after = parts
    if len(after) == 3 and len(before) <= 3:
        return None
    return decimal_number_key(f"{sign}{before}.{after}")


def decimal_number_key(value: str) -> str | None:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    normalized = number.normalize()
    if normalized == Decimal("-0"):
        normalized = Decimal("0")
    return f"number:{format(normalized, 'f')}"


def looks_like_untranslated_fallback(source_text: str, translated_text: str) -> bool:
    if source_text != translated_text:
        return False
    words = re.findall(r"\b[\w'-]+\b", source_text)
    letters = sum(1 for char in source_text if char.isalpha())
    return len(words) >= 3 and letters >= 12


def explicit_names_for_cue(cue: SourceCue) -> tuple[str, ...]:
    names: list[str] = []
    for key in ("protected_names", "names", "entities"):
        value = cue.metadata.get(key)
        if not value:
            continue
        if isinstance(value, str):
            names.append(value)
        else:
            for item in value:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, Mapping) and item.get("text") and item.get("type") in ("person", "place", "organization", "name"):
                    names.append(str(item["text"]))
    return tuple(dict.fromkeys(name for name in names if name))


def high_confidence_names(text: str) -> tuple[str, ...]:
    blocked = {"OK", "TV", "AI", "AM", "PM"}
    names = [
        token
        for token in re.findall(r"\b[A-Z][A-Z0-9&.-]{2,}\b", text)
        if token not in blocked and any(char.isalpha() for char in token)
    ]
    return tuple(dict.fromkeys(names))


def uncertain_entity_warnings(text: str, source_language: str) -> tuple[str, ...]:
    if normalize_open_language(source_language) == "de":
        return ()
    candidates = re.findall(r"\b[A-Z][a-z][A-Za-z'-]{2,}(?:\s+[A-Z][a-z][A-Za-z'-]{2,})+\b", text)
    return tuple(dict.fromkeys(candidates))


def is_retryable_malformed_error(error: ValueError) -> bool:
    text = str(error).lower()
    markers = (
        "preserve unit ids",
        "malformed translation response",
        "returned an empty unit",
        "empty text",
        "untranslated source text",
        "preserve markup",
        "preserve number",
        "preserve name",
        "apply glossary",
        "truncated",
        "interrupted",
        "different number",
    )
    return any(marker in text for marker in markers)


def raw_cue_digest(cues: tuple[RawTranslationCue, ...]) -> str:
    return stable_json_digest(
        {
            "raw_cues": [
                {
                    "id": cue.id,
                    "source_ids": cue.source_ids,
                    "start": cue.start,
                    "end": cue.end,
                    "text": cue.text,
                    "language": cue.language,
                    "source_text": cue.source_text,
                    "speaker": cue.speaker,
                    "metadata": thaw_json(cue.metadata),
                }
                for cue in cues
            ]
        }
    )


def collect_translation_warnings(
    raw_final_cues: tuple[RawTranslationCue, ...],
    raw_intermediate_cues: tuple[RawTranslationCue, ...],
    final_cues: tuple[SubtitleCue, ...],
    intermediate_cues: tuple[SubtitleCue, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    for cue in (*raw_intermediate_cues, *raw_final_cues, *intermediate_cues, *final_cues):
        if cue.metadata.get("timing_estimated"):
            warnings.append("timing_estimated")
        for warning in cue.metadata.get("validation_warnings") or ():
            warnings.append(str(warning))
        for warning in cue.metadata.get("warnings") or ():
            warnings.append(str(warning))
    return tuple(dict.fromkeys(warnings))


def normalize_subtitle_text(text: str) -> str:
    return " ".join(str(text).strip().replace("-->", "->").split())


def split_text_for_layout(text: str, source_duration: float, settings: TranslationSettings) -> list[str]:
    duration_chunks = max(1, math.ceil(max(0.0, source_duration) / settings.max_duration))
    cps_chunks = max(1, math.ceil(len(text) / max(1.0, settings.max_cps * settings.max_duration)))
    min_chunks = max(duration_chunks, cps_chunks)
    chunks = pack_caption_tokens(layout_tokens(text), settings)
    while len(chunks) < min_chunks:
        longest_index = max(range(len(chunks)), key=lambda index: len(chunks[index]))
        split = split_chunk(chunks[longest_index])
        if split is None:
            break
        chunks[longest_index:longest_index + 1] = split
    return chunks or [text]


def layout_tokens(text: str) -> list[str]:
    pattern = re.compile(r"(<[^>]+>|\{\\[^}]+\}|\[/?[A-Za-z][^\]]*\])")
    tokens: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        tokens.extend(normalize_subtitle_text(text[cursor:match.start()]).split())
        tokens.append(match.group(0))
        cursor = match.end()
    tokens.extend(normalize_subtitle_text(text[cursor:]).split())
    return [token for token in tokens if token]


def wrap_chunk_for_subtitle(chunk: str, settings: TranslationSettings) -> str:
    tokens = layout_tokens(chunk)
    if not tokens:
        return ""
    lines = pack_words(tokens, settings.max_chars_per_line)
    if len(lines) <= settings.max_lines:
        return "\n".join(lines)
    return "\n".join(collapse_overflow_lines(lines, settings.max_lines))


def collapse_overflow_lines(lines: list[str], max_lines: int) -> list[str]:
    if max_lines <= 1:
        return [" ".join(lines)]
    head = lines[:max_lines - 1]
    tail = " ".join(lines[max_lines - 1:])
    return [*head, tail]


def pack_caption_tokens(tokens: list[str], settings: TranslationSettings) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for token in tokens:
        candidate = [*current, token]
        if current and len(pack_words(candidate, settings.max_chars_per_line)) > settings.max_lines:
            chunks.append(" ".join(current))
            current = [token]
        else:
            current = candidate
    if current:
        chunks.append(" ".join(current))
    return chunks


def pack_words(words: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        candidate_len = len(word) if not current else current_len + 1 + len(word)
        if current and candidate_len > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = candidate_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def split_chunk(chunk: str) -> tuple[str, str] | None:
    words = chunk.split()
    if len(words) < 2:
        return None
    midpoint = len(words) // 2
    return " ".join(words[:midpoint]), " ".join(words[midpoint:])


def proportional_spans(start: float, end: float, chunks: list[str]) -> list[tuple[float, float]]:
    if len(chunks) == 1:
        return [(start, end)]
    duration = max(0.0, end - start)
    weights = [max(1, len(chunk)) for chunk in chunks]
    total = sum(weights)
    spans = []
    cursor = start
    for index, weight in enumerate(weights, start=1):
        if index == len(weights):
            chunk_end = end
        else:
            chunk_end = start + duration * (sum(weights[:index]) / total)
        spans.append((cursor, min(end, max(cursor, chunk_end))))
        cursor = chunk_end
    return spans


def flatten(items: list[tuple[SubtitleCue, ...]]) -> list[SubtitleCue]:
    flattened: list[SubtitleCue] = []
    for group in items:
        flattened.extend(group)
    return flattened


def thaw_json(value: Any) -> Any:
    if isinstance(value, (dict, MappingProxyType)):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [thaw_json(item) for item in sorted(value)]
    return value


def split_source_cue_for_translation(cue: SourceCue) -> list[SourceCue] | None:
    words = cue.text.split()
    if len(words) < 2:
        return None
    midpoint = len(words) // 2
    parts = (" ".join(words[:midpoint]), " ".join(words[midpoint:]))
    spans = split_spans_from_word_anchors(cue, midpoint, list(parts))
    return [
        SourceCue(
            id=cue.id,
            index=cue.index,
            start=start,
            end=end,
            text=text,
            language=cue.language,
            speaker=cue.speaker,
            words=cue.words,
            metadata={
                **dict(cue.metadata),
                "split_part": index,
                "split_count": 2,
                "original_text": cue.metadata.get("original_text", cue.text),
                "source_cue_anchors": source_cue_anchors_for(cue),
            },
        )
        for index, (text, (start, end)) in enumerate(zip(parts, spans), start=1)
    ]


def split_spans_from_word_anchors(
    cue: SourceCue,
    midpoint: int,
    parts: list[str],
) -> list[tuple[float, float]]:
    if len(cue.words) >= len(parts) and len(cue.words) >= midpoint + 1:
        first_words = cue.words[:midpoint]
        second_words = cue.words[midpoint:]
        if first_words and second_words:
            return [
                (cue.start, min(cue.end, max(cue.start, first_words[-1].end))),
                (max(cue.start, min(cue.end, second_words[0].start)), cue.end),
            ]
    return proportional_spans(cue.start, cue.end, parts)


def cue_digest(cues: tuple[SourceCue, ...]) -> str:
    return stable_json_digest(
        {
            "cues": [
                {
                    "id": cue.id,
                    "start": cue.start,
                    "end": cue.end,
                    "text": cue.text,
                    "language": cue.language,
                    "speaker": cue.speaker,
                    "source_text": cue.metadata.get("original_text"),
                }
                for cue in cues
            ]
        }
    )


def automatic_context_windows(cues: tuple[SourceCue, ...]) -> dict[str, tuple[SourceCue, ...]]:
    windows: dict[str, tuple[SourceCue, ...]] = {}
    for index, cue in enumerate(cues):
        nearby: list[SourceCue] = []
        left = index - 1
        right = index + 1
        while len(nearby) < 6 and (left >= 0 or right < len(cues)):
            if left >= 0:
                candidate = cues[left]
                if within_context_span(candidate, cue):
                    nearby.insert(0, candidate)
                left -= 1
            if len(nearby) >= 6:
                break
            if right < len(cues):
                candidate = cues[right]
                if within_context_span(candidate, cue):
                    nearby.append(candidate)
                right += 1
            if (left < 0 or not within_context_span(cues[left], cue)) and (right >= len(cues) or not within_context_span(cues[right], cue)):
                break
        windows[cue.id] = tuple(nearby)
    return windows


def within_context_span(candidate: SourceCue, cue: SourceCue) -> bool:
    return max(candidate.end, cue.end) - min(candidate.start, cue.start) <= 20.0


def automatic_context_for_unit(
    unit: list[SourceCue],
    context_windows: dict[str, tuple[SourceCue, ...]],
    manual_context: str,
) -> str:
    pieces = [manual_context.strip()] if manual_context.strip() else []
    seen = set()
    unit_ids = {cue.id for cue in unit}
    unit_source_ids = {source_id for cue in unit for source_id in source_ids_for_cue(cue)}
    for cue in unit:
        for context_cue in context_windows.get(cue.id, ()):
            if context_cue.id in unit_ids or any(source_id in unit_source_ids for source_id in source_ids_for_cue(context_cue)):
                continue
            context = context_cue.text
            if context and context not in seen:
                pieces.append(context)
                seen.add(context)
    return "\n".join(pieces)

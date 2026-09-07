from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol

from .model_manager import ModelSpec


TranslationEngineName = Literal["local", "google"]
TranslationRoute = Literal["direct", "via-en"]
TranslationStage = Literal["source", "intermediate", "final"]
TranslationStatus = Literal[
    "idle",
    "source_cached",
    "route_preview",
    "downloading",
    "preparing",
    "ready",
    "translating",
    "writing",
    "completed",
    "failed",
    "cancelled",
    "notice",
    "waiting",
]


ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SourceWord:
    id: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SourceCue:
    id: str
    index: int
    start: float
    end: float
    text: str
    language: str
    speaker: str | None = None
    words: tuple[SourceWord, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "words", tuple(self.words))
        object.__setattr__(self, "metadata", deep_freeze(self.metadata))


@dataclass(frozen=True)
class SubtitleCue:
    id: str
    source_ids: tuple[str, ...]
    start: float
    end: float
    text: str
    language: str
    source_text: str | None = None
    speaker: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "metadata", deep_freeze(self.metadata))


@dataclass(frozen=True)
class RawTranslationCue:
    id: str
    source_ids: tuple[str, ...]
    start: float
    end: float
    text: str
    language: str
    source_text: str
    speaker: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "metadata", deep_freeze(self.metadata))


@dataclass(frozen=True)
class TranslationRouteLeg:
    source_language: str
    target_language: str
    engine: TranslationEngineName
    model_id: str | None = None
    model_revision: str | None = None
    family: str | None = None
    contextual: bool = False


@dataclass(frozen=True)
class TranslationRoutePreview:
    source_language: str
    target_language: str
    route: TranslationRoute
    engine: TranslationEngineName
    legs: tuple[TranslationRouteLeg, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "legs", tuple(self.legs))
        object.__setattr__(self, "warnings", tuple(self.warnings))


@dataclass(frozen=True)
class TranslationModelSelection:
    id: str
    family: str
    revision: str
    source: str | None
    target: str | None
    status: str
    download_size: int
    context_mode: str
    notice: str = ""


@dataclass(frozen=True)
class TranslationServiceEvent:
    state: str
    message: str = ""
    progress: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", deep_freeze(self.details))


@dataclass(frozen=True)
class TranslationServiceState:
    state: str = "idle"
    progress: float | None = None
    cancellable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", deep_freeze(self.details))


@dataclass(frozen=True)
class TranslationSettings:
    source_language: str | None
    target_language: str
    route: TranslationRoute = "direct"
    engine: TranslationEngineName = "local"
    model_id: str | None = None
    device: str = "auto"
    offline: bool = False
    bilingual: bool = False
    adaptive_layout: bool = True
    max_chars_per_line: int = 42
    max_lines: int = 2
    max_cps: float = 17.0
    min_duration: float = 1.0
    max_duration: float = 7.0
    max_merge_gap: float = 0.25
    max_cues_per_unit: int = 6
    max_unit_duration: float = 20.0
    revision: str = "translation-pipeline-v1"


@dataclass(frozen=True)
class TranslationRequest:
    source_key: str
    cues: tuple[SourceCue, ...]
    settings: TranslationSettings
    context: str = ""
    glossary: Mapping[str, str] | None = None
    cache_dir: str | None = None
    progress: ProgressCallback | None = None
    cancel: CancelCallback | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "cues", tuple(self.cues))
        validate_source_cues(self.cues)
        if self.glossary is not None:
            object.__setattr__(self, "glossary", deep_freeze(self.glossary))


@dataclass(frozen=True)
class TranslationResult:
    source_key: str
    source_language: str
    target_language: str
    route: TranslationRoute
    final_cues: tuple[SubtitleCue, ...]
    source_cues: tuple[SourceCue, ...]
    intermediate_cues: tuple[SubtitleCue, ...] = field(default_factory=tuple)
    raw_final_cues: tuple[RawTranslationCue, ...] = field(default_factory=tuple)
    raw_intermediate_cues: tuple[RawTranslationCue, ...] = field(default_factory=tuple)
    cache_hit: bool = False
    status: TranslationStatus = "completed"
    model_id: str | None = None
    model_revision: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "final_cues", tuple(self.final_cues))
        object.__setattr__(self, "source_cues", tuple(self.source_cues))
        object.__setattr__(self, "intermediate_cues", tuple(self.intermediate_cues))
        object.__setattr__(self, "raw_final_cues", tuple(self.raw_final_cues))
        object.__setattr__(self, "raw_intermediate_cues", tuple(self.raw_intermediate_cues))
        object.__setattr__(self, "warnings", tuple(self.warnings))


class TranslationEngine(Protocol):
    contextual: bool
    max_input_tokens: int

    def translate(
        self,
        texts: list[str],
        source_language: str,
        target_language: str,
        context: str = "",
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        ...

    def close(self) -> None:
        ...


def validate_source_cues(cues: tuple[SourceCue, ...]) -> None:
    seen: set[str] = set()
    previous_start = -math.inf
    for cue in cues:
        if cue.id in seen:
            raise ValueError(f"Duplicate source cue id: {cue.id}")
        seen.add(cue.id)
        if not math.isfinite(cue.start) or not math.isfinite(cue.end):
            raise ValueError(f"Source cue {cue.id} has non-finite timing")
        if cue.start < 0 or cue.end < cue.start:
            raise ValueError(f"Source cue {cue.id} has invalid timing")
        if cue.start < previous_start:
            raise ValueError("Source cues must be ordered by start time")
        previous_start = cue.start

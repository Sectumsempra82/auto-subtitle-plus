"""Desktop-only persistence; restored work never starts automatically."""

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
import sys
import uuid

from ..translation_pipeline import atomic_write_text

MEDIA_EXTENSIONS = frozenset((
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpeg", ".mpg", ".ts",
    ".mp3", ".ogg", ".wav", ".flac", ".m4a", ".wma", ".aac", ".opus",
))
MAX_QUEUE = 500


@dataclass
class QueueItem:
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    progress: float | None = 0.0
    stage: str = "Queued"
    error: str = ""
    outputs: list[str] = field(default_factory=list)
    elapsed: float = 0.0


def local_media_path(value: str) -> str:
    if "://" in value or value.startswith(("\\\\", "//")):
        raise ValueError("Only local files are accepted.")
    path = Path(value).expanduser().resolve()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Only local files are accepted.")
    if not path.is_file():
        raise ValueError(f"File not found: {path.name}")
    if path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError(f"Unsupported media file: {path.name}")
    return str(path)


class StateStore:
    def __init__(self, path: str | Path | None = None):
        base = Path.home() / ("Library/Application Support" if sys.platform == "darwin" else ".cache")
        self.path = Path(path) if path else Path(os.environ.get("LOCALAPPDATA", base)) / "AutoSubtitlePlus" / "desktop" / "state.json"
        self.legacy_path = None
        if path is None and sys.platform == "darwin" and "LOCALAPPDATA" not in os.environ:
            self.legacy_path = Path.home() / ".cache/AutoSubtitlePlus/desktop/state.json"

    def load(self) -> tuple[dict, list[QueueItem]]:
        path = self.path
        if not path.exists() and self.legacy_path is not None:
            path = self.legacy_path
        try:
            if path.stat().st_size > 2_000_000:
                return {}, []
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                return {}, []
            items = []
            for row in data.get("queue", [])[:MAX_QUEUE]:
                if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                    continue
                item = QueueItem(**{k: v for k, v in row.items() if k in QueueItem.__dataclass_fields__})
                item.id = item.id if isinstance(item.id, str) and item.id else uuid.uuid4().hex
                item.error = item.error if isinstance(item.error, str) else ""
                item.stage = item.stage if isinstance(item.stage, str) else "Queued"
                item.outputs = [path for path in item.outputs if isinstance(path, str)] if isinstance(item.outputs, list) else []
                item.elapsed = float(item.elapsed) if isinstance(item.elapsed, (int, float)) and math.isfinite(item.elapsed) else 0.0
                item.elapsed = max(0.0, item.elapsed)
                if item.progress is not None:
                    item.progress = max(0.0, min(1.0, float(item.progress))) if isinstance(item.progress, (int, float)) and math.isfinite(item.progress) else 0.0
                if item.status not in ("queued", "completed", "failed", "cancelled", "interrupted"):
                    item.status, item.stage, item.progress = "interrupted", "Interrupted", 0.0
                items.append(item)
            settings = data.get("settings", {})
            return settings if isinstance(settings, dict) else {}, items
        except (OSError, ValueError, TypeError):
            return {}, []

    def save(self, settings: dict, items: list[QueueItem]) -> None:
        payload = {"version": 1, "settings": settings, "queue": [asdict(item) for item in items[:MAX_QUEUE]]}
        atomic_write_text(str(self.path), json.dumps(payload, ensure_ascii=True, indent=2))

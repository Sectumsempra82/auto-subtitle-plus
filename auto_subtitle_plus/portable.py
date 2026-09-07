"""Portable frozen-runtime bootstrap helpers."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Iterable


DATA_DIR_ENV = "AUTO_SUBTITLE_PLUS_DATA_DIR"
INITIALIZED_ENV = "AUTO_SUBTITLE_PLUS_PORTABLE_INITIALIZED"
BUNDLE_DIR_ENV = "AUTO_SUBTITLE_PLUS_BUNDLE_DIR"
EFFECTIVE_DATA_DIR_ENV = "AUTO_SUBTITLE_PLUS_EFFECTIVE_DATA_DIR"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path | None:
    if not is_frozen():
        return None
    root = getattr(sys, "_MEIPASS", None)
    if root:
        return Path(root).resolve()
    return Path(sys.executable).resolve().parent


def executable_dir() -> Path:
    return Path(sys.executable).resolve().parent


def portable_data_dir() -> Path | None:
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    if is_frozen():
        return executable_dir() / "data"
    return None


def bootstrap() -> Path | None:
    data_dir = configure_portable_data_environment()
    root = bundle_root()
    if root is not None:
        os.environ[INITIALIZED_ENV] = "1"
        os.environ[BUNDLE_DIR_ENV] = str(root)
        prepend_existing_paths([root / "bin"])
    if data_dir is not None:
        os.environ[EFFECTIVE_DATA_DIR_ENV] = str(data_dir)
    return data_dir


def configure_portable_data_environment() -> Path | None:
    data_dir = portable_data_dir()
    if data_dir is None:
        return None
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = data_dir / "cache"
    hf_home = data_dir / "huggingface"
    os.environ["LOCALAPPDATA"] = str(data_dir)
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)
    os.environ["HF_HOME"] = str(hf_home)
    os.environ["HF_HUB_CACHE"] = str(hf_home / "hub")
    os.environ.pop("TRANSFORMERS_CACHE", None)
    os.environ["TORCH_HOME"] = str(data_dir / "torch")
    return data_dir


def initialized_metadata() -> dict[str, str | None]:
    return {
        "initialized": os.environ.get(INITIALIZED_ENV),
        "bundle_dir": os.environ.get(BUNDLE_DIR_ENV),
        "data_dir": os.environ.get(EFFECTIVE_DATA_DIR_ENV),
        "hf_home": os.environ.get("HF_HOME"),
        "hf_hub_cache": os.environ.get("HF_HUB_CACHE"),
        "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
        "torch_home": os.environ.get("TORCH_HOME"),
    }


def bundled_llama_runtime(device: str, release: str) -> Path | None:
    root = bundle_root()
    if root is None:
        return None
    directory = root / "runtimes" / f"llama-{release}-{device}"
    return directory if directory.is_dir() else None


def bundled_cuda_paths() -> list[Path]:
    root = bundle_root()
    if root is None:
        return []
    candidates = []
    for base in (root, root / "runtimes"):
        candidates.extend(
            [
                base / "nvidia" / "cublas" / "bin",
                base / "nvidia" / "cudnn" / "bin",
                base / "nvidia" / "cuda_nvrtc" / "bin",
            ]
        )
    return [path for path in candidates if path.is_dir()]


def sanitized_subprocess_env(prepend_paths: Iterable[Path | str] = ()) -> dict[str, str]:
    environment = os.environ.copy()
    root = bundle_root()
    if root is not None:
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in environment.get("PATH", "").split(os.pathsep)
            if entry and not _path_is_relative_to(entry, root)
        )
    prepend_existing_paths(prepend_paths, environment)
    return environment


def prepend_existing_paths(paths: Iterable[Path | str], environment: dict[str, str] | None = None) -> None:
    target = os.environ if environment is None else environment
    current_path = target.get("PATH", "")
    known_paths = {os.path.normcase(path) for path in current_path.split(os.pathsep) if path}
    additions = []
    for item in paths:
        path = Path(item)
        text = str(path)
        key = os.path.normcase(text)
        if path.is_dir() and key not in known_paths:
            additions.append(text)
            known_paths.add(key)
    if additions:
        target["PATH"] = os.pathsep.join(additions + ([current_path] if current_path else []))


def _path_is_relative_to(path: str, root: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False

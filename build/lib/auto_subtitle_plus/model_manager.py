"""Pinned, account-free model acquisition shared by the CLI and desktop host."""

from dataclasses import asdict, dataclass
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import shutil
import tempfile
import time

DEFAULT_TRANSLATION_MODEL = "hy-mt2-1.8b-q8"
LANGUAGES = {"en": "English", "it": "Italian", "fr": "French", "es": "Spanish", "de": "German", "pt": "Portuguese"}
_LANG_ALIASES = {value.lower(): key for key, value in LANGUAGES.items()}


class CancelledError(RuntimeError):
    pass


def check_cancel(cancel):
    if cancel is not None and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()):
        raise CancelledError("Operation cancelled")


def emit(progress, state, **details):
    if progress:
        progress({"state": state, **details})


def cache_root(cache_dir=None):
    root = Path(cache_dir) if cache_dir else Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "AutoSubtitlePlus"
    return root.resolve()


def normalize_language(language):
    if not language:
        raise ValueError("Source language is unknown; specify --language")
    value = language.lower().replace("_", "-")
    value = _LANG_ALIASES.get(value, value)
    if value not in LANGUAGES:
        raise ValueError(f"Language {language!r} is not in the validated translation catalog ({', '.join(LANGUAGES)})")
    return value


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str | None
    git_blob: str


@dataclass(frozen=True)
class ModelSpec:
    id: str
    repo_id: str
    revision: str
    family: str
    files: tuple[ModelFile, ...]
    license: str
    contextual: bool = False
    source: str | None = None
    target: str | None = None
    languages: tuple[str, ...] = ()

    @property
    def download_size(self):
        return sum(item.size for item in self.files)

    @property
    def url(self):
        return f"https://huggingface.co/{self.repo_id}"


def _catalog():
    rows = json.loads(Path(__file__).with_name("translation_catalog.json").read_text(encoding="utf-8"))
    result = []
    for row in rows:
        repo = row["repo_id"]
        source = target = None
        if "GGUF" in repo:
            family = "hy-mt2"
            variants = [("hy-mt2-1.8b-q8", "Q8_0"), ("hy-mt2-1.8b-q4", "Q4_K_M")] if "1.8B" in repo else [("hy-mt2-7b-q4", "Q4_K_M")]
        elif "m2m100" in repo:
            family, variants = "m2m100", [("m2m100-418m", None)]
        elif "nllb" in repo:
            family, variants = "nllb", [("nllb-600m" if "600M" in repo else "nllb-1.3b", None)]
        elif "madlad" in repo:
            family, variants = "madlad", [("madlad-3b", None)]
        else:
            family = "opus"
            source, target = repo.rsplit("-", 2)[1:]
            variants = [(f"opus-{source}-{target}", None)]
        for model_id, quantization in variants:
            files = row["files"]
            if quantization:
                files = [f for f in files if f["name"].endswith(f"{quantization}.gguf") or f["name"].startswith(("README", "LICENSE"))]
            result.append(ModelSpec(model_id, repo, row["revision"], family,
                                    tuple(ModelFile(**f) for f in files), row["license"],
                                    family == "hy-mt2", source, target,
                                    (source, target) if source else tuple(LANGUAGES)))
    return tuple(result)


MODELS = _catalog()


def resolve_model(model_id, source, target):
    source, target = normalize_language(source), normalize_language(target)
    if model_id == "opus-mt":
        model_id = f"opus-{source}-{target}"
    spec = next((m for m in MODELS if m.id == model_id), None)
    if spec is None:
        raise ValueError(f"No catalog model {model_id!r}; use --list-translation-models")
    if spec.source and (spec.source, spec.target) != (source, target):
        raise ValueError(f"{spec.id} only supports {spec.source} -> {spec.target}")
    if source not in spec.languages or target not in spec.languages:
        raise ValueError(f"{spec.id} has no validated {source} -> {target} direction")
    return spec


def list_models(source=None, target=None, route="direct"):
    if route not in ("direct", "via-en"):
        raise ValueError("Unknown translation route")
    if source:
        source = normalize_language(source)
    if target:
        target = normalize_language(target)
    if route == "via-en" and "en" in (source, target):
        return []
    result = []
    for spec in MODELS:
        if source and source not in spec.languages or target and target not in spec.languages:
            continue
        if spec.source:
            if route == "via-en":
                continue
            if source and source != spec.source or target and target != spec.target:
                continue
        result.append(spec)
    return result


def source_dir(spec, cache_dir=None):
    return cache_root(cache_dir) / "models" / "source" / spec.repo_id.replace("/", "--") / spec.revision


def prepared_dir(spec, cache_dir=None):
    return cache_root(cache_dir) / "models" / "prepared" / f"{spec.id}-{spec.revision[:12]}-ct2-int8-v1"


def model_metadata(spec, cache_dir=None):
    raw = source_dir(spec, cache_dir)
    installed = all(verify_file(raw / f.name, f) for f in spec.files)
    if spec.family != "hy-mt2":
        installed = installed and _prepared_valid(prepared_dir(spec, cache_dir))
    return {**asdict(spec), "url": spec.url, "download_size": spec.download_size,
            "status": "installed" if installed else "not-installed",
            "context_mode": "dialogue" if spec.contextual else "sentence",
            "notice": "Noncommercial research model" if "nc" in spec.license else ""}


def verify_file(path, item, cancel=None):
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    digest = hashlib.sha256() if item.sha256 else hashlib.sha1()
    if not item.sha256:
        digest.update(f"blob {item.size}\0".encode())
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest() == (item.sha256 or item.git_blob)


def _download_worker(spec, directory, filenames, result):
    try:
        from huggingface_hub import hf_hub_download
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        for filename in filenames:
            hf_hub_download(spec.repo_id, filename, revision=spec.revision,
                            local_dir=directory, token=False)
        result.put(None)
    except Exception as error:
        result.put(f"Download failed: {type(error).__name__}: {error}")


def _convert_worker(source, destination, result):
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "1"
        from ctranslate2.converters import TransformersConverter
        TransformersConverter(source, load_as_float16=True, trust_remote_code=False).convert(destination, quantization="int8", force=True)
        result.put(None)
    except Exception as error:
        result.put(f"Model preparation failed: {type(error).__name__}: {error}")


def _run_worker(target, args, cancel=None, progress=None, state="preparing", directory=None, total=0):
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    process = context.Process(target=target, args=(*args, result))
    process.start()
    last = 0.0
    try:
        while process.is_alive():
            check_cancel(cancel)
            if time.monotonic() - last >= 0.5:
                downloaded = sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file()) if directory else 0
                emit(progress, state, completed_bytes=min(downloaded, total), total_bytes=total)
                last = time.monotonic()
            process.join(0.1)
        try:
            error = result.get(timeout=2)
        except queue.Empty:
            error = f"Model worker exited with code {process.exitcode}"
        if error:
            raise RuntimeError(error)
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        result.close()


def _prepared_valid(directory, cancel=None):
    try:
        receipt = json.loads((directory / "receipt.json").read_text())
        return bool(receipt) and all(
            (directory / f["name"]).resolve().is_relative_to(directory.resolve())
            and verify_file(directory / f["name"], ModelFile(**f), cancel) for f in receipt)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def ensure_model(spec, offline=False, progress=None, cancel=None, cache_dir=None):
    from filelock import FileLock, Timeout
    root = cache_root(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / f"{spec.repo_id.replace('/', '--')}.lock"))
    while True:
        check_cancel(cancel)
        try:
            lock.acquire(timeout=0.2)
            break
        except Timeout:
            emit(progress, "waiting", model=spec.id)
    try:
        raw = source_dir(spec, cache_dir)
        raw.mkdir(parents=True, exist_ok=True)
        missing = [f for f in spec.files if not verify_file(raw / f.name, f, cancel)]
        if missing:
            if offline:
                raise RuntimeError(f"{spec.id} is not completely installed or is corrupted; offline mode forbids downloads")
            needed = sum(f.size for f in missing)
            if shutil.disk_usage(root).free < needed + 100 * 1024 * 1024:
                raise RuntimeError(f"Insufficient disk space to download {spec.id} ({needed} bytes required)")
            for item in missing:
                path = raw / item.name
                if path.exists():
                    path.unlink()
            emit(progress, "downloading", model=spec.id, total_bytes=needed)
            _run_worker(_download_worker, (spec, str(raw), [f.name for f in missing]), cancel, progress, "downloading", raw, spec.download_size)
            if not all(verify_file(raw / f.name, f, cancel) for f in spec.files):
                raise RuntimeError(f"Integrity check failed for {spec.id}")
        if spec.family == "hy-mt2":
            emit(progress, "ready", model=spec.id)
            return raw / next(f.name for f in spec.files if f.name.endswith(".gguf"))
        prepared = prepared_dir(spec, cache_dir)
        if not _prepared_valid(prepared, cancel):
            if shutil.disk_usage(root).free < spec.download_size:
                raise RuntimeError("Insufficient free disk space for one-time model conversion")
            prepared.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix="prepare-", dir=prepared.parent))
            try:
                emit(progress, "preparing", model=spec.id)
                try:
                    _run_worker(_convert_worker, (str(raw), str(temporary)), cancel, progress)
                except RuntimeError as error:
                    if not str(error).startswith("Model worker exited with code"):
                        raise
                    check_cancel(cancel)
                    emit(progress, "notice", message="Model conversion worker stopped unexpectedly; retrying preparation once")
                    _run_worker(_convert_worker, (str(raw), str(temporary)), cancel, progress)
                files = []
                for path in temporary.iterdir():
                    if path.is_file():
                        with path.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        files.append(asdict(ModelFile(path.name, path.stat().st_size, digest, "")))
                if not (temporary / "model.bin").is_file():
                    raise RuntimeError("Conversion did not produce model.bin")
                (temporary / "receipt.json").write_text(json.dumps(files), encoding="utf-8")
                if prepared.exists():
                    shutil.rmtree(prepared)
                temporary.replace(prepared)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        emit(progress, "ready", model=spec.id)
        return prepared
    finally:
        lock.release()

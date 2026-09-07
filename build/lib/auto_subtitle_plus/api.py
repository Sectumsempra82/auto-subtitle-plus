from __future__ import annotations

import multiprocessing
import os
import queue
import threading
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable, Literal, Mapping

from .processing import ProcessingResult, job_needs_asr, run_job, terminate_process_tree
from .translation_pipeline import TranslationPipeline


JobStatus = Literal["completed", "failed", "cancelled"]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class JobOptions:
    path: str
    output_dir: str | None = None
    backend: str = "stable"
    model: str = "small"
    output_srt: bool = False
    output_audio: bool = False
    output_video: bool = False
    subtitle_format: str = "srt"
    output_txt: bool = False
    output_mkv: bool = False
    language: str | None = None
    translate_off: bool = False
    translate_to: str | None = None
    translation_engine: str = "local"
    translation_route: str = "direct"
    translation_model: str | None = None
    translation_device: str = "auto"
    translation_cache_dir: str | None = None
    retry_translation: bool = False
    offline: bool = False
    bilingual: bool = False
    output_source_subtitles: bool = False
    output_intermediate_subtitles: bool = False
    subtitle_layout: str = "adaptive"
    no_adaptive_layout: bool = False
    batch_size: int = 10
    max_workers: int = 4
    extract_workers: int | None = None
    device: str | None = None
    compute_type: str = "auto"
    inference_batch_size: int = 1
    vad: bool = False
    verbose: bool = False
    enhance_consistency: bool = False
    word_timestamps: bool = False
    max_chars_per_line: int = 42
    max_lines: int = 2
    max_cps: float = 17.0
    min_duration: float = 1.0
    max_duration: float = 7.0
    context: str = ""
    glossary: Mapping[str, str] | None = None
    overwrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))
        if self.output_dir is not None:
            object.__setattr__(self, "output_dir", str(self.output_dir))
        if self.glossary is not None:
            object.__setattr__(self, "glossary", MappingProxyType({str(k): str(v) for k, v in self.glossary.items()}))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "JobOptions":
        payload = dict(values)
        if "outputdir" in payload and "output_dir" not in payload:
            payload["output_dir"] = payload.pop("outputdir")
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"Unknown JobOptions field: {unknown[0]}")
        return cls(**payload)

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }
        if self.glossary is not None:
            payload["glossary"] = dict(self.glossary)
        return payload


@dataclass(frozen=True)
class JobResult:
    status: JobStatus
    outputs: tuple[str, ...] = ()
    error: str | None = None
    warnings: tuple[str, ...] = ()


class ParentJobRunner:
    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._commands = None
        self._responses = None
        self._process = None
        self._lock = threading.RLock()
        self._closed = False

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.is_alive() else None

    def run(
        self,
        options: JobOptions,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> JobResult:
        if not isinstance(options, JobOptions):
            options = JobOptions.from_mapping(options)
        with self._lock:
            if self._closed:
                raise RuntimeError("JobRunner is closed")
            self._ensure_worker()
            assert self._commands is not None
            assert self._responses is not None
            self._commands.put(("run", options.to_mapping()))
            while True:
                if cancel is not None and cancel():
                    self._terminate_worker(restart=False)
                    return JobResult(status="cancelled", error="Job cancelled")
                try:
                    kind, payload = self._responses.get(timeout=0.1)
                except queue.Empty:
                    process = self._process
                    if process is None or not process.is_alive():
                        code = None if process is None else process.exitcode
                        return JobResult(status="failed", error=f"Job worker exited with code {code}")
                    continue
                if kind == "progress":
                    if progress is not None:
                        progress(payload)
                elif kind == "result":
                    return result_from_mapping(payload)
                elif kind == "error":
                    return JobResult(status="failed", error=str(payload))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            if process is not None and process.is_alive() and self._commands is not None:
                self._commands.put(("close", None))
                process.join(timeout=2)
            self._terminate_worker(restart=False)

    def _ensure_worker(self) -> None:
        if self._process is None or not self._process.is_alive():
            self._start_worker()

    def _start_worker(self) -> None:
        self._close_queues()
        self._commands = self._context.Queue()
        self._responses = self._context.Queue()
        self._process = self._context.Process(target=_worker_main, args=(self._commands, self._responses))
        self._process.start()

    def _terminate_worker(self, restart: bool = True) -> None:
        process = self._process
        if process is None:
            return
        terminate_process_tree(process.pid)
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join()
        self._process = None
        self._close_queues()
        if restart and not self._closed:
            self._start_worker()

    def _close_queues(self) -> None:
        for pipe in (self._commands, self._responses):
            if pipe is not None:
                pipe.cancel_join_thread()
                pipe.close()
        self._commands = None
        self._responses = None


JobRunner = ParentJobRunner


def _worker_main(commands, responses) -> None:
    pipeline = None
    try:
        while True:
            command, payload = commands.get()
            if command == "close":
                break
            if command != "run":
                responses.put(("error", "Unknown worker command"))
                continue
            try:
                options = JobOptions.from_mapping(payload)
                if job_needs_asr(namespace_from_options(options)):
                    if pipeline is not None:
                        pipeline.close()
                        pipeline = None
                if pipeline is None:
                    pipeline = TranslationPipeline()
                result = run_job(
                    namespace_from_options(options),
                    progress=lambda event: responses.put(("progress", event)),
                    cancel=None,
                    pipeline=pipeline,
                    close_translation_runtime_on_finish=False,
                    stdout=None,
                )
                responses.put(("result", result_to_mapping(result)))
            except BaseException as error:
                responses.put(("error", str(error)))
    finally:
        if pipeline is not None:
            pipeline.close()


def namespace_from_options(options: JobOptions) -> SimpleNamespace:
    values = options.to_mapping()
    values["paths"] = [values.pop("path")]
    if values["extract_workers"] is None:
        values.pop("extract_workers")
    return SimpleNamespace(**values)


def result_to_mapping(result: ProcessingResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "outputs": tuple(result.outputs),
        "error": result.error,
        "warnings": tuple(result.warnings),
    }


def result_from_mapping(payload: Mapping[str, Any]) -> JobResult:
    status = payload.get("status")
    if status not in ("completed", "failed", "cancelled"):
        status = "failed"
    return JobResult(
        status=status,
        outputs=tuple(payload.get("outputs") or ()),
        error=payload.get("error"),
        warnings=tuple(payload.get("warnings") or ()),
    )

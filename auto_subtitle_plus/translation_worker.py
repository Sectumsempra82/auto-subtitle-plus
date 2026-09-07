"""Persistent, cancellable process boundary for native sequence-to-sequence inference."""

import multiprocessing
import os
import queue
import threading
import time

from .model_manager import check_cancel


def _serve(spec, path, device, allow_cpu, cache_dir, commands, responses):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from .local_translation import CT2TranslationEngine
    engine = None
    try:
        engine = CT2TranslationEngine(spec, path, device, allow_cpu,
                                      lambda event: responses.put(("progress", event)), None, cache_dir)
        responses.put(("ready", engine.device))
        while True:
            command = commands.get()
            if command is None:
                break
            try:
                result = engine.translate(*command)
                responses.put(("result", (result, engine.device)))
            except Exception as error:
                responses.put(("error", (type(error).__name__, str(error))))
    except Exception as error:
        responses.put(("error", (type(error).__name__, str(error))))
    finally:
        if engine is not None:
            engine.close()


class CT2WorkerEngine:
    contextual = False
    max_input_tokens = 480

    def __init__(self, spec, path, device, allow_cpu=False, progress=None, cancel=None, cache_dir=None):
        self.spec, self.device = spec, device
        self.progress, self.cancel = progress, cancel
        self.closed = False
        self._lock = threading.RLock()
        context = multiprocessing.get_context("spawn")
        self._commands, self._responses = context.Queue(), context.Queue()
        self.process = context.Process(target=_serve, args=(spec, path, device, allow_cpu, cache_dir,
                                                          self._commands, self._responses))
        self.process.start()
        try:
            self.device = self._wait("ready", 180)
        except BaseException:
            self.close()
            raise

    def _wait(self, expected, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            check_cancel(self.cancel)
            try:
                kind, payload = self._responses.get(timeout=0.1)
            except queue.Empty:
                if not self.process.is_alive():
                    raise RuntimeError(f"Translation worker exited with code {self.process.exitcode}")
                continue
            if kind == "progress":
                if self.progress:
                    self.progress(payload)
            elif kind == "error":
                name, message = payload
                raise ValueError(message) if name == "ValueError" else RuntimeError(message)
            elif kind == expected:
                return payload
            else:
                raise RuntimeError("Unexpected translation worker response")
        raise RuntimeError("Local translation worker timed out")

    def translate(self, texts, source_language, target_language, context="", glossary=None):
        while not self._lock.acquire(timeout=0.1):
            check_cancel(self.cancel)
        try:
            if self.closed:
                raise RuntimeError("Translation worker is closed")
            check_cancel(self.cancel)
            self._commands.put((texts, source_language, target_language, context, glossary))
            result, self.device = self._wait("result")
            return result
        except ValueError:
            raise
        except BaseException:
            self.close()
            raise
        finally:
            self._lock.release()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.is_alive():
            self._commands.put(None)
            self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
        else:
            self.process.join()
        self._commands.cancel_join_thread()
        self._responses.cancel_join_thread()
        self._commands.close()
        self._responses.close()

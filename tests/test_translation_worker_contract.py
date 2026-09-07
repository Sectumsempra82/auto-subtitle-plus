import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from auto_subtitle_plus import local_translation, model_manager, translation_worker


class FakeQueue:
    def __init__(self):
        self.items = []
        self.closed = False
        self.cancelled_join = False
        self.on_put = None

    def put(self, item):
        self.items.append(item)
        if self.on_put is not None:
            self.on_put(item)

    def get(self, timeout=None):
        if not self.items:
            raise queue.Empty()
        return self.items.pop(0)

    def close(self):
        self.closed = True

    def cancel_join_thread(self):
        self.cancelled_join = True


class FakeProcess:
    def __init__(self, responses=None, startup_items=(), stay_alive_after_stop=False):
        self.responses = responses
        self.startup_items = list(startup_items)
        self.stay_alive_after_stop = stay_alive_after_stop
        self.started = False
        self.terminated = False
        self.killed = False
        self.joins = []
        self.exitcode = None
        self.alive = False

    def start(self):
        self.started = True
        self.alive = True
        for item in self.startup_items:
            self.responses.put(item)

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)

    def terminate(self):
        self.terminated = True
        if not self.stay_alive_after_stop:
            self.alive = False
            self.exitcode = -15

    def kill(self):
        self.killed = True
        self.alive = False
        self.exitcode = -9


class FakeContext:
    def __init__(self, process):
        self.commands = FakeQueue()
        self.responses = FakeQueue()
        self.process = process
        self.process.responses = self.responses

    def Queue(self):
        if not hasattr(self, "_queue_count"):
            self._queue_count = 0
        self._queue_count += 1
        return self.commands if self._queue_count == 1 else self.responses

    def Process(self, target, args):
        self.target = target
        self.args = args
        return self.process


class CT2WorkerEngineContractTests(unittest.TestCase):
    def fake_spec(self):
        return SimpleNamespace(id="opus-en-fr", family="opus", revision="rev1")

    def make_engine(self, process, progress=None, cancel=None):
        context = FakeContext(process)
        with mock.patch.object(translation_worker.multiprocessing, "get_context", return_value=context):
            engine = translation_worker.CT2WorkerEngine(
                self.fake_spec(),
                Path("prepared-model"),
                "cuda",
                allow_cpu=True,
                progress=progress,
                cancel=cancel,
                cache_dir="cache",
            )
        return engine, context

    def test_worker_startup_progress_ready_and_translate_reuses_process(self):
        progress_events = []
        process = FakeProcess(
            startup_items=[
                ("progress", {"state": "loading", "model": "opus-en-fr"}),
                ("ready", "cuda"),
            ]
        )
        engine, context = self.make_engine(process, progress=progress_events.append)

        def respond_to_command(item):
            if item is not None:
                context.responses.put(("result", (["bonjour"], "cuda")))

        context.commands.on_put = respond_to_command
        result = engine.translate(["hello"], "en", "fr", context="", glossary=None)

        self.assertTrue(process.started)
        self.assertEqual(progress_events, [{"state": "loading", "model": "opus-en-fr"}])
        self.assertEqual(result, ["bonjour"])
        self.assertEqual(engine.device, "cuda")
        self.assertEqual(context.commands.items[0], (["hello"], "en", "fr", "", None))
        self.assertIs(engine.process, process)
        engine.close()

    def test_worker_startup_error_closes_process_and_surfaces_runtime_error(self):
        process = FakeProcess(startup_items=[("error", ("RuntimeError", "converter crashed"))])
        context = FakeContext(process)

        with mock.patch.object(translation_worker.multiprocessing, "get_context", return_value=context):
            with self.assertRaisesRegex(RuntimeError, "converter crashed"):
                translation_worker.CT2WorkerEngine(self.fake_spec(), Path("prepared"), "cpu")

        self.assertTrue(process.started)
        self.assertTrue(context.commands.closed)
        self.assertTrue(context.responses.closed)

    def test_worker_translate_value_error_surfaces_without_closing_process(self):
        process = FakeProcess(startup_items=[("ready", "cpu")])
        engine, context = self.make_engine(process)

        def respond_to_command(item):
            if item is not None:
                context.responses.put(("error", ("ValueError", "input exceeds budget")))

        context.commands.on_put = respond_to_command

        with self.assertRaisesRegex(ValueError, "input exceeds budget"):
            engine.translate(["too long"], "en", "fr")

        self.assertFalse(engine.closed)
        self.assertTrue(process.is_alive())
        engine.close()

    def test_worker_cancel_during_inference_terminates_native_process(self):
        process = FakeProcess(startup_items=[("ready", "cpu")], stay_alive_after_stop=True)
        calls = 0

        def cancel_after_request():
            nonlocal calls
            calls += 1
            return calls > 1

        engine, _context = self.make_engine(process, cancel=cancel_after_request)

        with self.assertRaises(model_manager.CancelledError):
            engine.translate(["hello"], "en", "fr")

        self.assertTrue(engine.closed)
        self.assertTrue(process.terminated)

    def test_worker_close_terminates_then_kills_if_process_ignores_shutdown(self):
        process = FakeProcess(startup_items=[("ready", "cpu")], stay_alive_after_stop=True)
        engine, context = self.make_engine(process)

        engine.close()

        self.assertIn(None, context.commands.items)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(context.commands.cancelled_join)
        self.assertTrue(context.responses.cancelled_join)

    def test_seq2seq_factory_uses_worker_engine_and_reuses_existing_instance(self):
        created = []

        class FakeWorkerEngine:
            contextual = False
            max_input_tokens = 480

            def __init__(self, spec, path, device, allow_cpu, progress, cancel, cache_dir):
                created.append((spec.id, path, device, allow_cpu, progress, cancel, cache_dir))
                self.closed = False
                self.progress = progress
                self.cancel = cancel
                self.device = device

            def close(self):
                self.closed = True

        try:
            with mock.patch.object(local_translation, "_gpu_available", return_value=False), \
                 mock.patch.object(local_translation, "ensure_model", return_value=Path("prepared")), \
                 mock.patch.object(translation_worker, "CT2WorkerEngine", FakeWorkerEngine):
                first = local_translation.get_translation_engine(
                    "opus-en-fr",
                    "en",
                    "fr",
                    device="cpu",
                    offline=True,
                    progress=lambda _event: None,
                    cancel=lambda: False,
                    cache_dir="cache-a",
                )
                second = local_translation.get_translation_engine(
                    "opus-en-fr",
                    "en",
                    "fr",
                    device="cpu",
                    offline=True,
                    progress=None,
                    cancel=lambda: True,
                    cache_dir="cache-a",
                )
        finally:
            local_translation.close_translation_engines()

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertIsNone(second.progress)
        self.assertTrue(second.cancel())


if __name__ == "__main__":
    unittest.main()

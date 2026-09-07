from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report_path = smoke_report_path(args)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "failed",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_dir": os.environ.get("AUTO_SUBTITLE_PLUS_DATA_DIR"),
        "state_path": str(report_path.parent / "gui-state" / "state.json"),
        "media": str(Path(args.smoke_media).resolve()) if args.smoke_media else None,
        "checks": [],
    }
    try:
        run_qt_smoke(args, report_path, report)
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        write_report(report_path, report)
        return 1
    write_report(report_path, report)
    return 0 if report["status"] == "passed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen GUI portable smoke check.")
    parser.add_argument("--smoke-test", action="store_true", help="Launch the real MainWindow, capture a smoke report, and exit")
    parser.add_argument("--smoke-run", action="store_true", help="Start the real MainWindow queue and wait for the smoke media job")
    parser.add_argument("--portable-smoke", default=None, help="Write GUI smoke JSON report here and exit")
    parser.add_argument("--smoke-media", default=None, help="Optional media file to enqueue in the real MainWindow")
    parser.add_argument("--smoke-output-dir", default=None, help="Output directory to validate in GUI settings")
    parser.add_argument("--smoke-asr-model", default=None, help="ASR model name or absolute local snapshot path for GUI settings validation")
    parser.add_argument("--smoke-translation-cache-dir", default=None, help="Local translation cache root for GUI settings validation")
    parser.add_argument("--smoke-timeout-ms", type=int, default=5000, help="Maximum Qt event loop duration")
    return parser


def smoke_report_path(args: argparse.Namespace) -> Path:
    if args.portable_smoke:
        return Path(args.portable_smoke).resolve()
    if args.smoke_test:
        data_dir = os.environ.get("AUTO_SUBTITLE_PLUS_DATA_DIR")
        root = Path(data_dir).resolve() if data_dir else Path.cwd().resolve()
        return root / "smoke" / "gui-smoke-report.json"
    raise SystemExit("--smoke-test or --portable-smoke is required")


def run_qt_smoke(args: argparse.Namespace, report_path: Path, report: dict[str, Any]) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from auto_subtitle_plus.desktop.state import StateStore
    from auto_subtitle_plus.desktop.window import MainWindow, apply_theme

    app = QApplication.instance() or QApplication([sys.argv[0], "--portable-smoke"])
    app.setApplicationName("Auto Subtitle Plus")
    app.setOrganizationName("AutoSubtitlePlus")
    apply_theme(app)

    state_path = report_path.parent / "gui-state" / "state.json"
    store = StateStore(state_path)
    window = MainWindow(store=store, monitor=False)
    report["checks"].append({"name": "construct-main-window", "status": "passed"})

    media_added = 0
    if args.smoke_media:
        media_added = window.add_paths([args.smoke_media])
        report["checks"].append({"name": "enqueue-smoke-media", "status": "passed" if media_added == 1 else "failed", "added": media_added})

    settings = smoke_settings(args)
    if settings:
        window.settings.restore(settings)
        window.settings.options()
        report["checks"].append({"name": "validate-smoke-options", "status": "passed"})

    window.show()
    app.processEvents()
    screenshot = report_path.with_name("gui-smoke.png")
    if window.grab().save(str(screenshot)):
        report["screenshot"] = str(screenshot)
        report["checks"].append({"name": "capture-main-window", "status": "passed"})
    else:
        report["checks"].append({"name": "capture-main-window", "status": "failed"})

    if args.smoke_run:
        if not args.smoke_media:
            report["checks"].append({"name": "run-smoke-job", "status": "failed", "error": "--smoke-run requires --smoke-media"})
        else:
            run_window_job(app, window, args, report)

    def close_window() -> None:
        window.close()
        app.quit()

    QTimer.singleShot(0, close_window)
    QTimer.singleShot(max(1000, args.smoke_timeout_ms), app.quit)
    app.exec()

    report["window_title"] = window.windowTitle()
    report["queue_count"] = len(window.items)
    report["items"] = queue_report(window)
    failures = [check for check in report["checks"] if check["status"] != "passed"]
    report["status"] = "failed" if failures else "passed"


def run_window_job(app: Any, window: Any, args: argparse.Namespace, report: dict[str, Any]) -> None:
    from PySide6.QtCore import QTimer

    started = time.monotonic()
    deadline = started + max(1.0, args.smoke_timeout_ms / 1000)
    window.start_queue()
    if not window.running:
        report["checks"].append({"name": "start-smoke-job", "status": "failed", "items": queue_report(window)})
        return
    report["checks"].append({"name": "start-smoke-job", "status": "passed"})
    state = {"done": False, "timed_out": False}

    def poll() -> None:
        if not window.running and not any(item.status in ("queued", "running") for item in window.items):
            state["done"] = True
            app.quit()
            return
        if time.monotonic() >= deadline:
            state["done"] = True
            state["timed_out"] = True
            if window.worker and window.worker.isRunning():
                window.worker.cancel_event.set()
                window.worker.wait(5000)
            window.running = False
            app.quit()
            return
        QTimer.singleShot(250, poll)

    QTimer.singleShot(250, poll)
    QTimer.singleShot(max(1000, args.smoke_timeout_ms + 1000), app.quit)
    app.exec()
    items = queue_report(window)
    if state["timed_out"]:
        report["checks"].append({"name": "complete-smoke-job", "status": "failed", "error": "timeout", "items": items})
        return
    completed = bool(items) and all(item["status"] == "completed" for item in items)
    outputs = [output for item in items for output in item["outputs"]]
    report["checks"].append(
        {
            "name": "complete-smoke-job",
            "status": "passed" if completed and outputs else "failed",
            "seconds": round(time.monotonic() - started, 3),
            "items": items,
            "outputs": outputs,
        }
    )


def queue_report(window: Any) -> list[dict[str, Any]]:
    return [
        {
            "path": item.path,
            "status": item.status,
            "stage": item.stage,
            "progress": item.progress,
            "error": item.error,
            "outputs": list(item.outputs),
        }
        for item in window.items
    ]


def smoke_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if args.smoke_output_dir:
        settings.update({"output_location": "folder", "output_dir": str(Path(args.smoke_output_dir).resolve())})
    if args.smoke_asr_model:
        settings.update({"backend": "faster", "model": args.smoke_asr_model, "device": "cpu", "compute_type": "int8"})
    if args.smoke_media:
        settings.update({"language": "en", "translate_enabled": True, "translate_to": "fr", "translation_engine": "local",
                         "translation_model": "opus-en-fr", "translation_device": "cpu", "output_srt": True,
                         "output_txt": True, "offline": True, "overwrite": False})
    if args.smoke_translation_cache_dir:
        settings["translation_cache_dir"] = str(Path(args.smoke_translation_cache_dir).resolve())
    return settings


def write_report(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


if __name__ == "__main__":
    raise SystemExit(main())

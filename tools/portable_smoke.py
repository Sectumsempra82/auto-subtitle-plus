from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_TRANSLATION_MODEL = "opus-en-fr"


class SmokeFailure(RuntimeError):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = run_smoke(args)
    write_json(Path(args.output_dir) / "portable-smoke-report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Windows portable packaging smoke verifier for PyInstaller onedir CLI/GUI builds. "
            "It runs built executables in a deliberately sparse environment and writes a structured report."
        )
    )
    parser.add_argument("--cli", required=True, help="Path to the packaged CLI .exe")
    parser.add_argument("--gui", default=None, help="Optional path to the packaged GUI .exe")
    parser.add_argument("--output-dir", required=True, help="Directory for verifier outputs and JSON report")
    parser.add_argument("--model-cache", default=None, help="Optional existing AutoSubtitlePlus model cache directory")
    parser.add_argument("--fixture", default=None, help="Optional tiny audio/video fixture for real ASR + local translation")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--asr-model", default=None, help="ASR model name or absolute local snapshot path; required with --fixture for offline verification")
    parser.add_argument("--language", default="en")
    parser.add_argument("--translate-to", default="fr")
    parser.add_argument("--translation-model", default=DEFAULT_TRANSLATION_MODEL)
    parser.add_argument("--gui-data-dir", default=None, help="Optional isolated portable data root for GUI smoke")
    parser.add_argument("--gui-smoke-run", action="store_true", help="Ask the GUI smoke contract to start and wait for the real queued job")
    return parser


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cli = require_exe(args.cli, "CLI")
    gui = require_exe(args.gui, "GUI") if args.gui else None
    model_cache = Path(args.model_cache).resolve() if args.model_cache else None
    if model_cache is not None and not model_cache.exists():
        raise SmokeFailure(f"model cache does not exist: {model_cache}")

    report: dict[str, Any] = {
        "status": "passed",
        "platform": sys.platform,
        "cli": str(cli),
        "gui": str(gui) if gui else None,
        "output_dir": str(output_dir.resolve()),
        "model_cache": str(model_cache) if model_cache else None,
        "environment": {
            "minimal_path": minimal_windows_path(),
            "cleared": sorted(cleared_environment_keys()),
            "isolated_home": True,
            "offline": True,
        },
        "checks": [],
    }

    checks = [
        ("cli-help", [str(cli), "--help"], ("usage:", "Auto Subtitle Plus")),
        ("cli-list-models", [str(cli), "--list-models"], ("model",)),
        ("cli-list-translation-models", [str(cli), "--list-translation-models"], ("->",)),
    ]

    for name, command, expected in checks:
        report["checks"].append(run_command_check(name, command, expected, args.timeout_seconds, model_cache=None))

    if gui is not None:
        report["checks"].append(run_gui_check(args, gui, output_dir, model_cache))

    if args.fixture:
        report["checks"].append(run_fixture_check(args, cli, output_dir, model_cache))
    else:
        report["checks"].append({"name": "fixture-asr-translation", "status": "skipped", "reason": "--fixture was not provided"})

    failures = [check for check in report["checks"] if check["status"] == "failed"]
    if failures:
        report["status"] = "failed"
    report["seconds"] = round(time.perf_counter() - started, 3)
    return report


def run_fixture_check(args: argparse.Namespace, cli: Path, output_dir: Path, model_cache: Path | None) -> dict[str, Any]:
    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        return failed_check("fixture-asr-translation", f"fixture does not exist: {fixture}")
    if not args.asr_model:
        return failed_check("fixture-asr-translation", "--asr-model is required with --fixture so offline ASR uses an explicit cached model path")

    work_dir = output_dir / "fixture-output"
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(cli),
        str(fixture),
        "--backend",
        "faster",
        "--model",
        str(Path(args.asr_model).resolve()) if Path(args.asr_model).exists() else args.asr_model,
        "--device",
        "cpu",
        "--compute-type",
        "int8",
        "--language",
        args.language,
        "--translate-to",
        args.translate_to,
        "--translation-backend",
        "local",
        "--translation-model",
        args.translation_model,
        "--translation-device",
        "cpu",
        "--offline",
        "--output-srt",
        "--output-txt",
        "--output-dir",
        str(work_dir),
    ]
    if model_cache is not None:
        command.extend(["--translation-cache-dir", str(model_cache)])

    result = run_command_check("fixture-asr-translation", command, (), args.timeout_seconds, model_cache=None)
    expected = [
        work_dir / f"{fixture.stem}.srt",
        work_dir / f"{fixture.stem}.txt",
    ]
    missing = [str(path) for path in expected if not path.exists()]
    if missing:
        result["status"] = "failed"
        result["error"] = f"missing expected output file(s): {', '.join(missing)}"
    else:
        result["outputs"] = [str(path) for path in expected]
    return result


def run_gui_check(args: argparse.Namespace, gui: Path, output_dir: Path, model_cache: Path | None) -> dict[str, Any]:
    report_path = output_dir / "gui-smoke-report.json"
    gui_output = output_dir / "gui-output"
    data_dir = Path(args.gui_data_dir).resolve() if args.gui_data_dir else output_dir / "gui-data"
    command = [
        str(gui),
        "--smoke-test",
        "--portable-smoke",
        str(report_path),
        "--smoke-output-dir",
        str(gui_output),
    ]
    if args.gui_smoke_run:
        command.append("--smoke-run")
    if args.fixture:
        command.extend(["--smoke-media", str(Path(args.fixture).resolve())])
    if args.asr_model:
        command.extend(["--smoke-asr-model", str(Path(args.asr_model).resolve()) if Path(args.asr_model).exists() else args.asr_model])
    if model_cache is not None:
        command.extend(["--smoke-translation-cache-dir", str(model_cache)])

    result = run_command_check("gui-portable-smoke", command, (), args.timeout_seconds, model_cache=None, data_dir=data_dir)
    if report_path.exists():
        try:
            result["gui_report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            result["status"] = "failed"
            result["error"] = f"cannot read GUI smoke report: {error}"
    elif result["status"] == "passed":
        result["status"] = "failed"
        result["error"] = f"GUI smoke report was not written: {report_path}"
    if result.get("gui_report", {}).get("status") != "passed":
        result["status"] = "failed"
        result.setdefault("error", "GUI smoke report did not pass")
    return result


def run_command_check(
    name: str,
    command: list[str],
    expected_output: tuple[str, ...],
    timeout_seconds: float,
    model_cache: Path | None,
    data_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="asp-portable-smoke-") as tmp:
        env = smoke_environment(Path(tmp), model_cache, data_dir)
        env.update(extra_env or {})
        try:
            process = subprocess.Popen(
                command,
                cwd=tmp,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess_creationflags(),
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                terminate_spawned_process_tree(process)
                stdout, stderr = process.communicate(timeout=10)
                return {
                    "name": name,
                    "status": "failed",
                    "error": f"timeout after {timeout_seconds} seconds; terminated spawned process tree rooted at PID {process.pid}",
                    "command": command,
                    "stdout_tail": tail(stdout),
                    "stderr_tail": tail(stderr),
                    "seconds": round(time.perf_counter() - started, 3),
                }
        except OSError as error:
            return failed_check(name, f"{type(error).__name__}: {error}", command=command)

    combined = f"{stdout}\n{stderr}".lower()
    missing = [needle for needle in expected_output if needle.lower() not in combined]
    status = "passed" if process.returncode == 0 and not missing else "failed"
    result = {
        "name": name,
        "status": status,
        "command": command,
        "returncode": process.returncode,
        "stdout_tail": tail(stdout),
        "stderr_tail": tail(stderr),
        "seconds": round(time.perf_counter() - started, 3),
    }
    if missing:
        result["error"] = f"missing expected output text: {', '.join(missing)}"
    elif process.returncode != 0:
        result["error"] = f"process exited with code {process.returncode}"
    return result


def smoke_environment(temp_home: Path, model_cache: Path | None, data_dir: Path | None = None) -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    portable_data = (data_dir or temp_home / "data").resolve()
    env = {
        "PATH": minimal_windows_path(system_root),
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "AUTO_SUBTITLE_PLUS_DATA_DIR": str(portable_data),
        "USERPROFILE": str(temp_home),
        "APPDATA": str(temp_home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(temp_home / "AppData" / "Local"),
        "TEMP": str(temp_home),
        "TMP": str(temp_home),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    if model_cache is not None:
        env["AUTO_SUBTITLE_PLUS_TRANSLATION_CACHE_DIR"] = str(model_cache)
    return env


def cleared_environment_keys() -> set[str]:
    return {
        key
        for key in os.environ
        if key.upper().startswith("PYTHON")
        or key.upper() in {"HOME", "VIRTUAL_ENV", "CONDA_PREFIX"}
    }


def minimal_windows_path(system_root: str | None = None) -> str:
    root = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows"))
    entries = [root / "System32", root]
    existing = [str(path) for path in entries if path.exists()]
    return os.pathsep.join(existing)


def require_exe(value: str | None, label: str) -> Path:
    if not value:
        raise SmokeFailure(f"{label} executable path is required")
    path = Path(value).resolve()
    if not path.exists():
        raise SmokeFailure(f"{label} executable does not exist: {path}")
    if path.suffix.lower() != ".exe":
        raise SmokeFailure(f"{label} path is not an .exe: {path}")
    return path


def terminate_spawned_process_tree(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32" and shutil.which("taskkill"):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


def subprocess_creationflags() -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def failed_check(name: str, error: str, command: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": "failed", "error": error}
    if command is not None:
        result["command"] = command
    return result


def tail(value: str | None, limit: int = 4000) -> str:
    if not value:
        return ""
    return value[-limit:]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as error:
        print(f"portable smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)

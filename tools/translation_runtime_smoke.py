from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict, dataclass
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class SmokeCase:
    name: str
    family: str
    model_id: str
    source: str
    target: str
    texts: tuple[str, ...]
    context: str = ""


DEFAULT_CASES = (
    SmokeCase("hy-1.8b-q8-it-fr", "hy", "hy-mt2-1.8b-q8", "it", "fr", ("Ciao, come stai oggi?",), "Friendly dialogue."),
    SmokeCase("hy-1.8b-q4-fr-en", "hy", "hy-mt2-1.8b-q4", "fr", "en", ("Bonjour, comment allez-vous aujourd'hui?",), "Polite dialogue."),
    SmokeCase("hy-7b-q4-en-de", "hy", "hy-mt2-7b-q4", "en", "de", ("The meeting starts at ten.",), "Business dialogue."),
    SmokeCase("m2m100-418m-it-fr", "m2m", "m2m100-418m", "it", "fr", ("La riunione inizia alle dieci.",)),
    SmokeCase("nllb-600m-en-fr", "nllb", "nllb-600m", "en", "fr", ("Please close the window.",)),
    SmokeCase("nllb-1.3b-fr-en", "nllb", "nllb-1.3b", "fr", "en", ("Veuillez fermer la fenetre.",)),
    SmokeCase("opus-en-it", "opus", "opus-en-it", "en", "it", ("The train is late.",)),
    SmokeCase("opus-it-en", "opus", "opus-it-en", "it", "en", ("Il treno e in ritardo.",)),
    SmokeCase("opus-en-fr", "opus", "opus-en-fr", "en", "fr", ("The train is late.",)),
    SmokeCase("opus-fr-en", "opus", "opus-fr-en", "fr", "en", ("Le train est en retard.",)),
    SmokeCase("opus-en-es", "opus", "opus-en-es", "en", "es", ("The train is late.",)),
    SmokeCase("opus-es-en", "opus", "opus-es-en", "es", "en", ("El tren llega tarde.",)),
    SmokeCase("opus-en-de", "opus", "opus-en-de", "en", "de", ("The train is late.",)),
    SmokeCase("opus-de-en", "opus", "opus-de-en", "de", "en", ("Der Zug hat Verspatung.",)),
    SmokeCase("madlad-3b-en-fr", "madlad", "madlad-3b", "en", "fr", ("The lights are still on.",)),
)


class SmokeFailure(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Offline clean-Windows local translation runtime smoke harness. "
            "Requires already installed catalog models and blocks non-loopback network use."
        )
    )
    parser.add_argument("--devices", nargs="+", choices=("cpu", "cuda"), default=["cpu"])
    parser.add_argument("--families", nargs="+", choices=("hy", "m2m", "nllb", "opus", "madlad"), default=["hy", "m2m", "nllb", "opus", "madlad"])
    parser.add_argument("--case", action="append", dest="case_names", help="Run one case by name; may be repeated")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--keep-current-path", action="store_true", help="Do not replace PATH with a minimal Python/Windows PATH in child cases.")
    parser.add_argument("--child-case-json", default=None)
    parser.add_argument("--child-output-json", default=None)
    args = parser.parse_args()

    if args.child_case_json:
        if not args.child_output_json:
            raise SystemExit("--child-output-json is required with --child-case-json")
        return run_child(args)

    cases = select_cases(args)
    report = {
        "offline": True,
        "loopback_only_network": True,
        "minimal_path": not args.keep_current_path,
        "devices": args.devices,
        "case_count": len(cases) * len(args.devices),
        "results": [],
    }
    output = Path(args.output_json) if args.output_json else Path.cwd() / "translation-runtime-smoke-report.json"
    atomic_write_json(output, report)

    failed = False
    for case in cases:
        for device in args.devices:
            result = run_case_subprocess(case, device, args)
            report["results"].append(result)
            atomic_write_json(output, report)
            print(f"{case.name} {device}: {result['status']}", flush=True)
            failed = failed or result["status"] != "passed"

    return int(failed)


def run_child(args: argparse.Namespace) -> int:
    case = SmokeCase(**json.loads(args.child_case_json))
    started = time.perf_counter()
    result: dict[str, Any] = {"case": case.name, "family": case.family, "model": case.model_id, "device": args.devices[0], "status": "failed"}
    try:
        configure_offline_environment()
        if not args.keep_current_path:
            os.environ["PATH"] = minimal_windows_python_path()
        if os.environ.get("ASP_SMOKE_NETWORK_GUARD") == "1":
            result.update(run_runtime_case(case, args.devices[0], offline=True, cache_dir=args.cache_dir))
        else:
            with loopback_only_network_guard():
                result.update(run_runtime_case(case, args.devices[0], offline=True, cache_dir=args.cache_dir))
    except BaseException as error:
        result.update(error=f"{type(error).__name__}: {error}")
    finally:
        result["seconds"] = time.perf_counter() - started
        atomic_write_json(Path(args.child_output_json), result)
    return 0 if result["status"] == "passed" else 1


def run_runtime_case(case: SmokeCase, device: str, offline: bool, cache_dir: str | None) -> dict[str, Any]:
    from auto_subtitle_plus.local_translation import close_translation_engines, get_translation_engine
    from auto_subtitle_plus.model_manager import model_metadata, resolve_model

    spec = resolve_model(case.model_id, case.source, case.target)
    metadata = model_metadata(spec, cache_dir)
    if metadata["status"] != "installed":
        raise SmokeFailure(
            f"{case.model_id} {case.source}->{case.target} is {metadata['status']}; "
            "install the pinned catalog model before running this offline smoke case"
        )

    engine = None
    try:
        engine = get_translation_engine(case.model_id, case.source, case.target, device=device, offline=offline, cache_dir=cache_dir)
        process = getattr(engine, "process", None)
        if process is None:
            raise SmokeFailure(f"{type(engine).__name__} does not expose a managed process")
        if not process_is_running(process):
            raise SmokeFailure(f"{type(engine).__name__} process is not running before translation")
        output = engine.translate(list(case.texts), case.source, case.target, context=case.context if engine.contextual else "")
        if len(output) != len(case.texts):
            raise SmokeFailure(f"expected {len(case.texts)} output(s), got {len(output)}")
        if any(not isinstance(item, str) or not item.strip() for item in output):
            raise SmokeFailure("translation output contains an empty item")
        effective_device = getattr(engine, "device", device)
        engine.close()
        if not getattr(engine, "closed", False):
            raise SmokeFailure("engine did not mark itself closed after close()")
        if process_is_running(process):
            raise SmokeFailure(f"{type(engine).__name__} process survived close()")
        return {
            "status": "passed",
            "model_revision": spec.revision,
            "effective_device": effective_device,
            "engine_class": type(engine).__name__,
            "process_exitcode": process_exitcode(process),
            "output": output,
        }
    finally:
        try:
            if engine is not None and not getattr(engine, "closed", False):
                engine.close()
        finally:
            close_translation_engines()


def process_is_running(process: Any) -> bool:
    if hasattr(process, "poll"):
        return process.poll() is None
    if hasattr(process, "is_alive"):
        return process.is_alive()
    raise SmokeFailure(f"Unsupported process handle type: {type(process).__name__}")


def process_exitcode(process: Any) -> int | None:
    if hasattr(process, "returncode"):
        return process.returncode
    if hasattr(process, "exitcode"):
        return process.exitcode
    return None


def run_case_subprocess(case: SmokeCase, device: str, args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        child_output = Path(tmp) / "result.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child-case-json",
            json.dumps(asdict(case), ensure_ascii=True),
            "--child-output-json",
            str(child_output),
            "--devices",
            device,
            "--timeout-seconds",
            str(args.timeout_seconds),
        ]
        if args.cache_dir:
            command.extend(["--cache-dir", args.cache_dir])
        if args.keep_current_path:
            command.append("--keep-current-path")

        environment = os.environ.copy()
        configure_offline_environment(environment)
        environment["ASP_SMOKE_NETWORK_GUARD"] = "1"
        if not args.keep_current_path:
            environment["PATH"] = minimal_windows_python_path()

        try:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess_creationflags(),
            )
            try:
                stdout, stderr = process.communicate(timeout=args.timeout_seconds)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process)
                stdout, stderr = process.communicate(timeout=10)
                return {
                    "case": case.name,
                    "family": case.family,
                    "model": case.model_id,
                    "device": device,
                    "status": "failed",
                    "error": f"TimeoutExpired: exceeded {args.timeout_seconds} seconds; killed child process tree",
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                }
        except OSError as error:
            return {
                "case": case.name,
                "family": case.family,
                "model": case.model_id,
                "device": device,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "stdout": "",
                "stderr": "",
            }

        if child_output.exists():
            result = json.loads(child_output.read_text(encoding="utf-8"))
        else:
            result = {"case": case.name, "family": case.family, "model": case.model_id, "device": device, "status": "failed"}
        result["returncode"] = process.returncode
        if process.returncode != 0 and "error" not in result:
            result["error"] = f"child process exited with code {process.returncode}"
        if stdout.strip():
            result["stdout"] = stdout[-4000:]
        if stderr.strip():
            result["stderr"] = stderr[-4000:]
        return result


def subprocess_creationflags() -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def terminate_process_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    else:
        process.kill()


def select_cases(args: argparse.Namespace) -> list[SmokeCase]:
    selected = [case for case in DEFAULT_CASES if case.family in set(args.families)]
    if args.case_names:
        names = set(args.case_names)
        selected = [case for case in selected if case.name in names]
        missing = names - {case.name for case in selected}
        if missing:
            raise SystemExit(f"Unknown smoke case(s): {', '.join(sorted(missing))}")
    if not selected:
        raise SystemExit("No smoke cases selected")
    return selected


def configure_offline_environment(environment: dict[str, str] | None = None) -> None:
    target = os.environ if environment is None else environment
    target["HF_HUB_OFFLINE"] = "1"
    target["TRANSFORMERS_OFFLINE"] = "1"
    target["HF_HUB_DISABLE_TELEMETRY"] = "1"
    target["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "1"
    target["NO_PROXY"] = "127.0.0.1,localhost,::1"
    target["no_proxy"] = "127.0.0.1,localhost,::1"
    for key in ("OLLAMA_HOST", "LLAMA_ARG_HOST", "LLAMA_HOST"):
        target.pop(key, None)


def minimal_windows_python_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        Path(sys.executable).parent,
        Path(sys.prefix),
        Path(sys.prefix) / "Scripts",
        Path(sys.prefix) / "DLLs",
        Path(sys.prefix) / "Library" / "bin",
        Path(system_root) / "System32",
        Path(system_root),
        Path(system_root) / "System32" / "Wbem",
    ]
    entries = []
    seen = set()
    for path in candidates:
        text = str(path)
        key = text.lower()
        if key in seen or not path.exists():
            continue
        seen.add(key)
        entries.append(text)
    return os.pathsep.join(entries)


@contextlib.contextmanager
def loopback_only_network_guard():
    original_connect = socket.socket.connect

    def guarded_connect(sock: socket.socket, address: Any):
        host = address[0] if isinstance(address, tuple) and address else address
        if not is_loopback_host(str(host)):
            raise SmokeFailure(f"blocked non-loopback socket connection to {host!r}")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect
    requests_patch = patch_requests_session()
    try:
        yield
    finally:
        socket.socket.connect = original_connect
        if requests_patch is not None:
            requests_module, original_request = requests_patch
            requests_module.sessions.Session.request = original_request


def patch_requests_session():
    try:
        import requests
    except ImportError:
        return None
    original_request = requests.sessions.Session.request

    def guarded_request(self, method, url, *args, **kwargs):
        parsed = urlparse(str(url))
        if parsed.hostname and not is_loopback_host(parsed.hostname):
            raise SmokeFailure(f"blocked non-loopback HTTP request to {url}")
        return original_request(self, method, url, *args, **kwargs)

    requests.sessions.Session.request = guarded_request
    return requests, original_request


def is_loopback_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise


_NETWORK_GUARD_CONTEXT = None
if os.environ.get("ASP_SMOKE_NETWORK_GUARD") == "1":
    _NETWORK_GUARD_CONTEXT = loopback_only_network_guard()
    _NETWORK_GUARD_CONTEXT.__enter__()


if __name__ == "__main__":
    raise SystemExit(main())

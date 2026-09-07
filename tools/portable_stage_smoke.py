"""Validate frozen Stable Whisper, model acquisition/conversion, and a cold GUI job."""
import argparse
import json
import os
from pathlib import Path
import shutil

from auto_subtitle_plus.model_manager import resolve_model, source_dir
from tools.portable_smoke import run_command_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--gui", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--asr-model", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--stable-weights", type=Path, required=True, help="Existing small.pt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory for cold-stage validation")
    output.mkdir(parents=True)
    fixture = str(args.fixture.resolve())
    cli = str(args.cli.resolve())
    results = []

    data = output / "stable-data"
    weights = data / "cache/whisper/small.pt"
    weights.parent.mkdir(parents=True)
    os.link(args.stable_weights.resolve(), weights)
    command = [cli, fixture, "--backend", "stable", "--model", "small", "--language", "en",
               "--device", "cpu", "--offline", "--output-srt", "--output-dir", str(output / "stable-output")]
    results.append(run_command_check("stable-cpu-offline", command, ("Subtitles saved",), 600, None, data_dir=data))

    cache = output / "conversion-cache"
    spec = resolve_model("opus-en-fr", "en", "fr")
    shutil.copytree(source_dir(spec, args.model_cache), source_dir(spec, cache), copy_function=os.link,
                    ignore=shutil.ignore_patterns("config.json"))
    command = [cli, fixture, "--backend", "faster", "--model", str(args.asr_model.resolve()),
               "--device", "cpu", "--compute-type", "int8", "--language", "en", "--translate-to", "fr",
               "--translation-model", "opus-en-fr", "--translation-device", "cpu",
               "--translation-cache-dir", str(cache), "--output-srt", "--output-dir", str(output / "conversion-output")]
    results.append(run_command_check("automatic-download-and-conversion", command,
                    ("Translation downloading", "Translation preparing", "Subtitles saved"), 600, None,
                    extra_env={"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"}))

    gui_cache = output / "gui-cache"
    shutil.copytree(cache / "models", gui_cache / "models", copy_function=os.link)
    gui_report = output / "gui-report.json"
    command = [str(args.gui.resolve()), "--portable-smoke", str(gui_report), "--smoke-media", fixture,
               "--smoke-run", "--smoke-asr-model", str(args.asr_model.resolve()),
               "--smoke-translation-cache-dir", str(gui_cache), "--smoke-output-dir", str(output / "gui-output"),
               "--smoke-timeout-ms", "600000"]
    result = run_command_check("cold-gui-job", command, (), 620, None, data_dir=output / "gui-data")
    if gui_report.is_file():
        result["gui_report"] = json.loads(gui_report.read_text())
        if result["gui_report"]["status"] != "passed":
            result["status"] = "failed"
    else:
        result["status"] = "failed"
    results.append(result)
    (output / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for result in results:
        print(f"{result['name']}: {result['status']} ({result.get('seconds')}s)", flush=True)
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())

"""Optional real-model validation of the frozen CLI, without downloading weights."""
import argparse
import json
import os
from pathlib import Path
import shutil

from tools.portable_smoke import run_command_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--asr-model", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="+", default=["hy-mt2-1.8b-q8", "m2m100-418m", "nllb-600m", "opus-en-it", "madlad-3b"])
    parser.add_argument("--devices", nargs="+", choices=["cpu", "cuda"], default=["cpu", "cuda"])
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = output / "cache"
    if cache.exists():
        parser.error("Use a new output directory so cached translations cannot bypass runtime validation")
    # Read-only model files get new directory entries, not another multi-GB copy.
    shutil.copytree(args.model_cache.resolve() / "models", cache / "models", copy_function=os.link)
    results = []
    cli = str(args.cli.resolve())
    for device in args.devices:
        for model in args.models:
            destination = output / f"{model}-{device}"
            destination.mkdir()
            command = [cli, str(args.fixture.resolve()), "--backend", "faster", "--model", str(args.asr_model.resolve()),
                       "--device", "cuda" if device == "cuda" else "cpu", "--compute-type", "float16" if device == "cuda" else "int8",
                       "--language", "en", "--translate-to", "it", "--translation-model", model,
                       "--translation-device", device, "--translation-cache-dir", str(cache), "--offline",
                       "--output-srt", "--output-txt", "--output-dir", str(destination)]
            result = run_command_check(f"{model}-{device}", command, ("Translation loading", "Subtitles saved"), args.timeout, None)
            result["output_exists"] = (destination / f"{args.fixture.stem}.srt").is_file()
            if not result["output_exists"]:
                result["status"] = "failed"
            results.append(result)
            (output / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"{model} {device}: {result['status']} ({result.get('seconds')}s)", flush=True)
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())

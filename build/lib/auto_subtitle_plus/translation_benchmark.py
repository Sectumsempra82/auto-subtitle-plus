"""Reference-based local translation evaluation, independent of ASR WER."""

import argparse
import json
from pathlib import Path
import subprocess
import threading
import time

from .local_translation import close_translation_engines, get_translation_engine
from .model_manager import DEFAULT_TRANSLATION_MODEL, normalize_language, resolve_model
from .translation_pipeline import atomic_write_text


class MemorySampler:
    def __init__(self):
        self.stop = threading.Event()
        self.peak_rss_bytes = 0
        self.peak_gpu_mib = None
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        import psutil
        process = psutil.Process()
        while not self.stop.is_set():
            processes = [process] + process.children(recursive=True)
            total, pids = 0, set()
            for child in processes:
                try:
                    total += child.memory_info().rss
                    pids.add(child.pid)
                except psutil.Error:
                    pass
            self.peak_rss_bytes = max(total, self.peak_rss_bytes)
            try:
                result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
                                        capture_output=True, text=True, timeout=3,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                gpu = 0
                available = False
                for line in result.stdout.splitlines():
                    pid, memory = (part.strip() for part in line.split(",", 1))
                    if int(pid) in pids and memory.isdigit():
                        gpu += int(memory)
                        available = True
                if available:
                    self.peak_gpu_mib = max(gpu, self.peak_gpu_mib or 0)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            self.stop.wait(0.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def read_examples(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in ("source", "target", "text", "reference")):
            raise ValueError(f"Line {number} requires source, target, text and reference strings")
        row["source"] = normalize_language(row["source"])
        row["target"] = normalize_language(row["target"])
        rows.append(row)
    if not rows:
        raise ValueError("No reference examples were supplied")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate local translation with human-reference JSONL examples")
    parser.add_argument("examples")
    parser.add_argument("--models", nargs="+", default=[DEFAULT_TRANSLATION_MODEL])
    parser.add_argument("--routes", nargs="+", choices=["direct", "via-en"], default=["direct", "via-en"])
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)
    try:
        from sacrebleu.metrics import CHRF
        examples = read_examples(args.examples)
    except (ImportError, OSError, ValueError) as error:
        parser.error(str(error))
    scorer = CHRF(word_order=2)
    report = {"metric": "chrF++ (word_order=2)", "reference_file": str(Path(args.examples).resolve()), "results": []}
    failed = False
    destination = Path(args.output_json)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(destination, json.dumps(report, indent=2, ensure_ascii=False))
    for model_id in args.models:
        for route in args.routes:
            for example in examples:
                source, target = example["source"], example["target"]
                if route == "via-en" and "en" in (source, target):
                    continue
                row = {"model": model_id, "route": route, "source": source, "target": target, "text": example["text"], "reference": example["reference"]}
                started = time.perf_counter()
                with MemorySampler() as memory:
                    try:
                        legs = [(source, "en"), ("en", target)] if route == "via-en" else [(source, target)]
                        specs = [resolve_model(model_id, a, b) for a, b in legs]
                        row["model_revisions"] = [spec.revision for spec in specs]
                        text = example["text"]
                        inference = 0.0
                        for index, (a, b) in enumerate(legs):
                            engine = get_translation_engine(model_id, a, b, args.device, args.offline)
                            before = time.perf_counter()
                            text = engine.translate([text], a, b, context=example.get("context", "") if engine.contextual and index == 0 else "")[0]
                            inference += time.perf_counter() - before
                            if route == "via-en" and index == 0:
                                row["intermediate"] = text
                        row.update(output=text, chrf_plus_plus=scorer.sentence_score(text, [example["reference"]]).score,
                                   inference_seconds=inference, characters_per_second=len(example["text"]) / max(inference, 1e-9), status="completed")
                    except Exception as error:
                        row.update(status="failed", error=f"{type(error).__name__}: {error}")
                        failed = True
                        close_translation_engines()
                row.update(total_seconds=time.perf_counter() - started, peak_rss_bytes=memory.peak_rss_bytes,
                           peak_gpu_mib=memory.peak_gpu_mib, gpu_measurement="sampled process memory; null when unavailable")
                report["results"].append(row)
                atomic_write_text(destination, json.dumps(report, indent=2, ensure_ascii=False))
                print(f"{model_id} {source}->{target} {route}: {row['status']}", flush=True)
        close_translation_engines()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

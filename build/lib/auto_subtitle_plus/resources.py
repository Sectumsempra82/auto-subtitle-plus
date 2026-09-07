import os
import subprocess
import time
from typing import Any

import psutil


class ResourceMonitor:
    def __init__(self, root_pid: int | None = None, disk_path: str | None = None):
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.disk_path = disk_path or os.getcwd()
        self._last_timestamp: float | None = None
        self._last_net = None
        self._network_rx_total = 0
        self._network_tx_total = 0
        self._last_gpu_timestamp: float | None = None
        self._last_gpu_result: tuple[list[dict[str, Any]] | None, str | None] | None = None
        self._processes: dict[tuple[int, float], psutil.Process] = {}

        psutil.cpu_percent(interval=None)
        self._prime_process_cpu()

    def sample(self) -> dict[str, Any]:
        timestamp = time.monotonic()
        interval = None if self._last_timestamp is None else max(0.0, timestamp - self._last_timestamp)

        system_cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        app_cpu, app_ram = self._sample_app_process_tree()
        rx_rate, tx_rate = self._sample_network(interval)
        gpus, gpu_error = self._sample_gpu(timestamp)
        try:
            disk = psutil.disk_usage(self.disk_path)
        except OSError:
            disk = None

        self._last_timestamp = timestamp

        return {
            "timestamp": timestamp,
            "interval": interval,
            "cpu_percent": self._clamp_percent(system_cpu),
            "app_cpu_percent": app_cpu,
            "ram_used": int(ram.used),
            "ram_total": int(ram.total),
            "app_ram": app_ram,
            "network_rx_rate": rx_rate,
            "network_tx_rate": tx_rate,
            "network_rx_total": self._network_rx_total,
            "network_tx_total": self._network_tx_total,
            "gpus": gpus,
            "gpu_error": gpu_error,
            "disk_free": int(disk.free) if disk else None,
            "disk_total": int(disk.total) if disk else None,
        }

    def close(self) -> None:
        return None

    def _prime_process_cpu(self) -> None:
        for process in self._process_tree():
            try:
                process.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

    def _sample_app_process_tree(self) -> tuple[float | None, int | None]:
        cpu_total = 0.0
        rss_total = 0
        found_process = False

        for process in self._process_tree():
            try:
                cpu_total += float(process.cpu_percent(interval=None))
                rss_total += int(process.memory_info().rss)
                found_process = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if not found_process:
            return None, None

        cpu_count = psutil.cpu_count() or 1
        return self._clamp_percent(cpu_total / cpu_count), rss_total

    def _process_tree(self):
        try:
            root = psutil.Process(self.root_pid)
            candidates = [root] + root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            self._processes.clear()
            return []
        processes = {}
        for candidate in candidates:
            try:
                key = (candidate.pid, candidate.create_time())
                # cpu_percent keeps its previous sample on the Process instance.
                processes[key] = self._processes.get(key, candidate)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self._processes = processes
        return list(processes.values())

    def _sample_network(self, interval: float | None) -> tuple[float | None, float | None]:
        counters = psutil.net_io_counters()
        if self._last_net is None:
            self._last_net = counters
            return None, None

        rx_delta = self._counter_delta(counters.bytes_recv, self._last_net.bytes_recv)
        tx_delta = self._counter_delta(counters.bytes_sent, self._last_net.bytes_sent)
        self._network_rx_total += rx_delta
        self._network_tx_total += tx_delta
        self._last_net = counters

        if interval is None or interval <= 0:
            return None, None

        return rx_delta / interval, tx_delta / interval

    def _sample_gpu(self, timestamp: float) -> tuple[list[dict[str, Any]] | None, str | None]:
        if (
            self._last_gpu_result is not None
            and self._last_gpu_timestamp is not None
            and timestamp - self._last_gpu_timestamp < 2.0
        ):
            return self._last_gpu_result

        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1,
                creationflags=self._hidden_process_flags(),
                check=False,
            )
        except FileNotFoundError:
            gpu_result = (None, "nvidia-smi unavailable")
        except subprocess.TimeoutExpired:
            gpu_result = (None, "nvidia-smi timed out")
        except OSError as exc:
            gpu_result = (None, str(exc))
        else:
            gpu_result = self._parse_gpu_result(result)

        self._last_gpu_timestamp = timestamp
        self._last_gpu_result = gpu_result
        return gpu_result

    @staticmethod
    def _parse_gpu_result(result: subprocess.CompletedProcess[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
        if result.returncode != 0:
            return None, (result.stderr or result.stdout or "nvidia-smi failed").strip()

        gpus: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 4:
                return None, "unexpected nvidia-smi output"

            name, utilization, used_mib, total_mib = parts
            try:
                gpu = {
                    "name": name,
                    "utilization": ResourceMonitor._clamp_percent(float(utilization)),
                    "used": int(float(used_mib) * 1024 * 1024),
                    "total": int(float(total_mib) * 1024 * 1024),
                }
            except ValueError:
                return None, "unexpected nvidia-smi output"
            gpus.append(gpu)

        return gpus, None

    @staticmethod
    def _counter_delta(current: int, previous: int) -> int:
        if current < previous:
            return 0
        return int(current - previous)

    @staticmethod
    def _clamp_percent(value: float) -> float:
        return max(0.0, min(100.0, float(value)))

    @staticmethod
    def _hidden_process_flags() -> int:
        return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

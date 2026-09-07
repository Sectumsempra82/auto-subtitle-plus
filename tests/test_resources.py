import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from auto_subtitle_plus.resources import ResourceMonitor


class FakeProcess:
    def __init__(self, cpu_values, rss, children=None, fail=False):
        self.pid = id(self)
        self.cpu_values = list(cpu_values)
        self.rss = rss
        self._children = children or []
        self.fail = fail

    def create_time(self):
        return 1.0

    def cpu_percent(self, interval=None):
        if self.fail:
            raise psutil_error()
        if not self.cpu_values:
            return 0
        return self.cpu_values.pop(0)

    def memory_info(self):
        if self.fail:
            raise psutil_error()
        return SimpleNamespace(rss=self.rss)

    def children(self, recursive=True):
        if self.fail:
            raise psutil_error()
        return self._children


def psutil_error():
    from auto_subtitle_plus import resources

    return resources.psutil.NoSuchProcess(123)


class ResourceMonitorTests(unittest.TestCase):
    def test_process_instances_retain_cpu_sampling_baseline(self):
        from auto_subtitle_plus import resources

        first = FakeProcess([0, 80], 100)
        second = FakeProcess([0], 100)
        second.pid = first.pid
        with mock.patch.object(resources.psutil, "Process", side_effect=[first, second]), \
             mock.patch.object(resources.psutil, "cpu_percent", return_value=0), \
             mock.patch.object(resources.psutil, "cpu_count", return_value=4):
            monitor = ResourceMonitor(root_pid=first.pid)
            cpu, memory = monitor._sample_app_process_tree()
        self.assertEqual(cpu, 20)
        self.assertEqual(memory, 100)

    def _common_psutil_patches(self, resources, process):
        return (
            mock.patch.object(resources.psutil, "cpu_percent", return_value=35.0),
            mock.patch.object(resources.psutil, "virtual_memory", return_value=SimpleNamespace(used=400, total=1000)),
            mock.patch.object(resources.psutil, "disk_usage", return_value=SimpleNamespace(free=700, total=900)),
            mock.patch.object(resources.psutil, "cpu_count", return_value=4),
            mock.patch.object(resources.psutil, "Process", return_value=process),
        )

    def test_first_sample_has_stable_json_schema_and_no_artificial_network_rate(self):
        from auto_subtitle_plus import resources

        child = FakeProcess([0, 20], 50)
        root = FakeProcess([0, 60], 100, [child])

        patches = self._common_psutil_patches(resources, root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(resources.psutil, "net_io_counters", return_value=SimpleNamespace(bytes_recv=1000, bytes_sent=2000)), \
             mock.patch.object(resources.time, "monotonic", return_value=10.0), \
             mock.patch.object(resources.subprocess, "run", side_effect=FileNotFoundError):
            sample = ResourceMonitor(root_pid=123, disk_path="C:/out").sample()

        self.assertEqual(
            list(sample.keys()),
            [
                "timestamp",
                "interval",
                "cpu_percent",
                "app_cpu_percent",
                "ram_used",
                "ram_total",
                "app_ram",
                "network_rx_rate",
                "network_tx_rate",
                "network_rx_total",
                "network_tx_total",
                "gpus",
                "gpu_error",
                "disk_free",
                "disk_total",
            ],
        )
        self.assertIsNone(sample["interval"])
        self.assertIsNone(sample["network_rx_rate"])
        self.assertIsNone(sample["network_tx_rate"])
        self.assertEqual(sample["network_rx_total"], 0)
        self.assertEqual(sample["network_tx_total"], 0)
        self.assertEqual(sample["app_cpu_percent"], 20.0)
        self.assertEqual(sample["app_ram"], 150)
        self.assertIsNone(sample["gpus"])
        self.assertEqual(sample["gpu_error"], "nvidia-smi unavailable")
        json.dumps(sample)

    def test_monotonic_interval_network_rates_and_totals_are_counter_reset_safe(self):
        from auto_subtitle_plus import resources

        root = FakeProcess([0, 10, 10, 10], 100)
        net_values = [
            SimpleNamespace(bytes_recv=1000, bytes_sent=2000),
            SimpleNamespace(bytes_recv=1300, bytes_sent=2600),
            SimpleNamespace(bytes_recv=100, bytes_sent=150),
        ]

        patches = self._common_psutil_patches(resources, root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(resources.psutil, "net_io_counters", side_effect=net_values), \
             mock.patch.object(resources.time, "monotonic", side_effect=[10.0, 12.0, 17.0]), \
             mock.patch.object(resources.subprocess, "run", side_effect=FileNotFoundError):
            monitor = ResourceMonitor(root_pid=123)
            first = monitor.sample()
            second = monitor.sample()
            third = monitor.sample()

        self.assertIsNone(first["interval"])
        self.assertEqual(second["interval"], 2.0)
        self.assertEqual(second["network_rx_rate"], 150.0)
        self.assertEqual(second["network_tx_rate"], 300.0)
        self.assertEqual(second["network_rx_total"], 300)
        self.assertEqual(second["network_tx_total"], 600)
        self.assertEqual(third["interval"], 5.0)
        self.assertEqual(third["network_rx_rate"], 0.0)
        self.assertEqual(third["network_tx_rate"], 0.0)
        self.assertEqual(third["network_rx_total"], 300)
        self.assertEqual(third["network_tx_total"], 600)

    def test_process_gone_returns_none_for_app_metrics(self):
        from auto_subtitle_plus import resources

        patches = self._common_psutil_patches(resources, FakeProcess([], 0, fail=True))
        with patches[0], patches[1], patches[2], patches[3], \
             mock.patch.object(resources.psutil, "Process", side_effect=resources.psutil.NoSuchProcess(123)), \
             mock.patch.object(resources.psutil, "net_io_counters", return_value=SimpleNamespace(bytes_recv=1, bytes_sent=1)), \
             mock.patch.object(resources.time, "monotonic", return_value=1.0), \
             mock.patch.object(resources.subprocess, "run", side_effect=FileNotFoundError):
            sample = ResourceMonitor(root_pid=123).sample()

        self.assertIsNone(sample["app_cpu_percent"])
        self.assertIsNone(sample["app_ram"])

    def test_gpu_query_parses_bytes_and_uses_two_second_cache(self):
        from auto_subtitle_plus import resources

        root = FakeProcess([0, 0, 0], 100)
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=0,
            stdout="RTX 4090, 75, 1024, 24576\n",
            stderr="",
        )

        patches = self._common_psutil_patches(resources, root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(resources.psutil, "net_io_counters", return_value=SimpleNamespace(bytes_recv=1, bytes_sent=1)), \
             mock.patch.object(resources.time, "monotonic", side_effect=[1.0, 2.0]), \
             mock.patch.object(resources.subprocess, "run", return_value=completed) as run:
            monitor = ResourceMonitor(root_pid=123)
            first = monitor.sample()
            second = monitor.sample()

        self.assertEqual(run.call_count, 1)
        self.assertEqual(first["gpus"], second["gpus"])
        self.assertEqual(first["gpus"][0]["name"], "RTX 4090")
        self.assertEqual(first["gpus"][0]["utilization"], 75.0)
        self.assertEqual(first["gpus"][0]["used"], 1024 * 1024 * 1024)
        self.assertEqual(first["gpus"][0]["total"], 24576 * 1024 * 1024)
        self.assertIsNone(first["gpu_error"])

    def test_gpu_unavailable_is_graceful(self):
        from auto_subtitle_plus import resources

        root = FakeProcess([0, 0], 100)
        completed = subprocess.CompletedProcess(
            args=["nvidia-smi"],
            returncode=1,
            stdout="",
            stderr="driver unavailable",
        )

        patches = self._common_psutil_patches(resources, root)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             mock.patch.object(resources.psutil, "net_io_counters", return_value=SimpleNamespace(bytes_recv=1, bytes_sent=1)), \
             mock.patch.object(resources.time, "monotonic", return_value=1.0), \
             mock.patch.object(resources.subprocess, "run", return_value=completed):
            sample = ResourceMonitor(root_pid=123).sample()

        self.assertIsNone(sample["gpus"])
        self.assertEqual(sample["gpu_error"], "driver unavailable")


if __name__ == "__main__":
    unittest.main()

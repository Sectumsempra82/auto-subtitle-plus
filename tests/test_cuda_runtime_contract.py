import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import cuda_runtime


class FakeDistribution:
    def __init__(self, root):
        self.root = Path(root)

    def locate_file(self, relative_path):
        return self.root / relative_path


class CudaRuntimeContractTests(unittest.TestCase):
    def test_non_windows_platform_is_noop(self):
        with mock.patch.object(cuda_runtime.sys, "platform", "linux"), \
             mock.patch.object(cuda_runtime, "distribution") as distribution, \
             mock.patch.object(cuda_runtime.os, "environ", {"PATH": "original"}):
            cuda_runtime.configure_windows_cuda()
            path = cuda_runtime.os.environ["PATH"]

        distribution.assert_not_called()
        self.assertEqual(path, "original")

    def test_absent_packages_are_noop(self):
        with mock.patch.object(cuda_runtime.sys, "platform", "win32"), \
             mock.patch.object(cuda_runtime, "distribution", side_effect=cuda_runtime.PackageNotFoundError), \
             mock.patch.object(cuda_runtime.os, "environ", {"PATH": "original"}):
            cuda_runtime.configure_windows_cuda()
            path = cuda_runtime.os.environ["PATH"]

        self.assertEqual(path, "original")

    def test_only_existing_official_distribution_paths_are_prepended(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cublas = root / "nvidia" / "cublas" / "bin"
            cudnn = root / "nvidia" / "cudnn" / "bin"
            cublas.mkdir(parents=True)
            cudnn.mkdir(parents=True)

            def fake_distribution(name):
                if name in {"nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12"}:
                    return FakeDistribution(root)
                raise AssertionError(f"unexpected package lookup: {name}")

            with mock.patch.object(cuda_runtime.sys, "platform", "win32"), \
                 mock.patch.object(cuda_runtime, "distribution", side_effect=fake_distribution), \
                 mock.patch.object(cuda_runtime.os, "environ", {"PATH": "original"}):
                cuda_runtime.configure_windows_cuda()
                path = cuda_runtime.os.environ["PATH"]

            parts = path.split(os.pathsep)
            self.assertEqual(parts[:2], [str(cublas), str(cudnn)])
            self.assertEqual(parts[-1], "original")
            self.assertNotIn(str(root / "nvidia" / "cuda_nvrtc" / "bin"), parts)

    def test_preserves_existing_path_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cublas = root / "nvidia" / "cublas" / "bin"
            cudnn = root / "nvidia" / "cudnn" / "bin"
            nvrtc = root / "nvidia" / "cuda_nvrtc" / "bin"
            for path in (cublas, cudnn, nvrtc):
                path.mkdir(parents=True)

            with mock.patch.object(cuda_runtime.sys, "platform", "win32"), \
                 mock.patch.object(cuda_runtime, "distribution", return_value=FakeDistribution(root)), \
                 mock.patch.object(cuda_runtime.os, "environ", {"PATH": os.pathsep.join([str(cublas), "tail"])}):
                cuda_runtime.configure_windows_cuda()
                first = cuda_runtime.os.environ["PATH"]
                cuda_runtime.configure_windows_cuda()
                second = cuda_runtime.os.environ["PATH"]

            self.assertEqual(first, second)
            parts = first.split(os.pathsep)
            self.assertEqual(parts.count(str(cublas)), 1)
            self.assertIn(str(cudnn), parts)
            self.assertIn(str(nvrtc), parts)
            self.assertTrue(first.endswith(os.pathsep.join([str(cublas), "tail"])))


if __name__ == "__main__":
    unittest.main()

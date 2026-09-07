import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_subtitle_plus import backends, portable


class PortableContractTests(unittest.TestCase):
    def test_source_install_bootstrap_is_noop_without_portable_data_override(self):
        with mock.patch.object(portable.sys, "frozen", False, create=True), \
             mock.patch.object(portable.sys, "_MEIPASS", None, create=True), \
             mock.patch.dict(portable.os.environ, {"PATH": "original"}, clear=True):
            result = portable.bootstrap()
            environment = dict(portable.os.environ)

        self.assertIsNone(result)
        self.assertEqual(environment, {"PATH": "original"})

    def test_frozen_bootstrap_uses_exe_sibling_data_and_bundle_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            exe_dir = root / "dist"
            bundle_bin = bundle / "bin"
            bundle_bin.mkdir(parents=True)
            exe_dir.mkdir()

            with mock.patch.object(portable.sys, "frozen", True, create=True), \
                 mock.patch.object(portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.object(portable.sys, "executable", str(exe_dir / "auto-subtitle-plus.exe")), \
                 mock.patch.dict(portable.os.environ, {"PATH": "original"}, clear=True):
                data_dir = portable.bootstrap()
                environment = dict(portable.os.environ)

        expected_data = exe_dir / "data"
        self.assertEqual(data_dir, expected_data)
        self.assertEqual(environment["LOCALAPPDATA"], str(expected_data))
        self.assertEqual(environment["XDG_CACHE_HOME"], str(expected_data / "cache"))
        self.assertEqual(environment["HF_HOME"], str(expected_data / "huggingface"))
        self.assertEqual(environment["HF_HUB_CACHE"], str(expected_data / "huggingface" / "hub"))
        self.assertNotIn("TRANSFORMERS_CACHE", environment)
        self.assertEqual(environment["TORCH_HOME"], str(expected_data / "torch"))
        self.assertEqual(environment[portable.INITIALIZED_ENV], "1")
        self.assertEqual(environment[portable.BUNDLE_DIR_ENV], str(bundle.resolve()))
        self.assertEqual(environment[portable.EFFECTIVE_DATA_DIR_ENV], str(expected_data))
        self.assertEqual(environment["PATH"].split(os.pathsep)[:2], [str(bundle_bin), "original"])

    def test_frozen_bootstrap_replaces_stale_cache_env_and_omits_deprecated_transformers_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            exe_dir = root / "dist"
            data = exe_dir / "data"
            bundle.mkdir()
            exe_dir.mkdir()

            with mock.patch.object(portable.sys, "frozen", True, create=True), \
                 mock.patch.object(portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.object(portable.sys, "executable", str(exe_dir / "auto-subtitle-plus.exe")), \
                 mock.patch.dict(
                     portable.os.environ,
                     {
                         "PATH": "original",
                         "XDG_CACHE_HOME": "C:/stale/xdg",
                         "HF_HOME": "C:/stale/hf",
                         "HF_HUB_CACHE": "C:/stale/hub",
                         "TRANSFORMERS_CACHE": "C:/stale/transformers",
                     },
                     clear=True,
                 ):
                portable.bootstrap()
                environment = dict(portable.os.environ)
                metadata = portable.initialized_metadata()

        self.assertEqual(environment["XDG_CACHE_HOME"], str(data / "cache"))
        self.assertEqual(environment["HF_HOME"], str(data / "huggingface"))
        self.assertEqual(environment["HF_HUB_CACHE"], str(data / "huggingface" / "hub"))
        self.assertNotIn("TRANSFORMERS_CACHE", environment)
        self.assertEqual(metadata["initialized"], "1")
        self.assertEqual(metadata["data_dir"], str(data))

    def test_bootstrap_redirects_stable_whisper_cache_used_by_offline_fingerprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            exe_dir = Path(tmp) / "dist"
            bundle.mkdir()
            exe_dir.mkdir()

            with mock.patch.object(portable.sys, "frozen", True, create=True), \
                 mock.patch.object(portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.object(portable.sys, "executable", str(exe_dir / "auto-subtitle-plus.exe")), \
                 mock.patch.dict(portable.os.environ, {"PATH": "original"}, clear=True):
                portable.bootstrap()
                cache_dir = backends.whisper_cache_dir()

        self.assertEqual(cache_dir, str(exe_dir / "data" / "cache" / "whisper"))

    def test_data_dir_override_applies_without_freezing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "portable-data"
            with mock.patch.object(portable.sys, "frozen", False, create=True), \
                 mock.patch.dict(portable.os.environ, {"PATH": "original", portable.DATA_DIR_ENV: str(data)}, clear=True):
                result = portable.bootstrap()
                environment = dict(portable.os.environ)

        self.assertEqual(result, data.resolve())
        self.assertEqual(environment["LOCALAPPDATA"], str(data.resolve()))
        self.assertEqual(environment["HF_HOME"], str(data.resolve() / "huggingface"))
        self.assertNotIn("TRANSFORMERS_CACHE", environment)
        self.assertEqual(environment[portable.EFFECTIVE_DATA_DIR_ENV], str(data.resolve()))
        self.assertEqual(environment["PATH"], "original")

    def test_sanitized_subprocess_env_removes_frozen_bundle_paths_and_prepends_explicit_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "bundle"
            bundled_bin = bundle / "bin"
            runtime_bin = bundle / "runtimes" / "llama-b10840-cuda" / "bin"
            outside = root / "outside"
            for path in (bundled_bin, runtime_bin, outside):
                path.mkdir(parents=True)
            original_path = os.pathsep.join([str(bundle), str(bundled_bin), str(outside)])

            with mock.patch.object(portable.sys, "frozen", True, create=True), \
                 mock.patch.object(portable.sys, "_MEIPASS", str(bundle), create=True), \
                 mock.patch.dict(portable.os.environ, {"PATH": original_path}, clear=True):
                environment = portable.sanitized_subprocess_env([runtime_bin])

        parts = environment["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], str(runtime_bin))
        self.assertIn(str(outside), parts)
        self.assertNotIn(str(bundle), parts)
        self.assertNotIn(str(bundled_bin), parts)

    def test_bundled_runtime_and_cuda_paths_are_discovered_under_meipass(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            runtime = bundle / "runtimes" / "llama-b10840-cpu"
            cublas = bundle / "nvidia" / "cublas" / "bin"
            cudnn = bundle / "runtimes" / "nvidia" / "cudnn" / "bin"
            for path in (runtime, cublas, cudnn):
                path.mkdir(parents=True)

            with mock.patch.object(portable.sys, "frozen", True, create=True), \
                 mock.patch.object(portable.sys, "_MEIPASS", str(bundle), create=True):
                self.assertEqual(portable.bundled_llama_runtime("cpu", "b10840"), runtime)
                cuda_paths = portable.bundled_cuda_paths()

        self.assertIn(cublas, cuda_paths)
        self.assertIn(cudnn, cuda_paths)


if __name__ == "__main__":
    unittest.main()

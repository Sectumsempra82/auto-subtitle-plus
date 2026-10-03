import importlib.util
import hashlib
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dependency_bootstrap", ROOT / "packaging/bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_installer_auto_choice_replaces_legacy_cpu_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "installation.ini").write_text("[Application]\nRuntime=auto\nRuntimeRevision=new\n")
            with patch.object(bootstrap, "default_runtime_device", return_value="cuda"):
                selected = bootstrap.runtime_selection(root, {"device": "cpu"})
            self.assertEqual(selected, {"device": "cuda", "selection": "auto", "installer_revision": "new"})

    def test_explicit_cpu_choice_survives_later_launches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "installation.ini").write_text("[Application]\nRuntime=auto\nRuntimeRevision=new\n")
            selected = bootstrap.runtime_selection(root, {}, "cpu")
            with patch.object(bootstrap, "default_runtime_device", side_effect=AssertionError("CPU must remain explicit")):
                self.assertEqual(bootstrap.runtime_selection(root, selected), selected)

    def test_updated_installer_cpu_choice_replaces_previous_cuda_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "installation.ini").write_text("[Application]\nRuntime=cpu\nRuntimeRevision=new\n")
            selected = bootstrap.runtime_selection(root, {"device": "cuda", "selection": "cuda", "installer_revision": "old"})
            self.assertEqual(selected["device"], "cpu")

    def test_auto_profile_rechecks_hardware_after_portable_move(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bootstrap, "default_runtime_device", return_value="cpu"):
                selected = bootstrap.runtime_selection(Path(temp), {"device": "cuda", "selection": "auto", "installer_revision": None})
            self.assertEqual(selected["device"], "cpu")

    def item(self, contents=b"verified"):
        return {"filename": "package.whl", "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest(),
                "url": "https://files.pythonhosted.org/package.whl"}

    def test_cached_download_is_verified_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "package.whl").write_bytes(b"verified")
            with patch.object(bootstrap, "urlopen", side_effect=AssertionError("network")):
                self.assertEqual(bootstrap.acquire(self.item(), path, path, True), path / "package.whl")

    def test_corrupt_and_partial_downloads_are_not_installed_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "package.whl").write_bytes(b"bad")
            (path / "package.whl.partial").write_bytes(b"verified")
            with self.assertRaisesRegex(RuntimeError, "Offline"):
                bootstrap.acquire(self.item(), path, path, True)

    def test_unapproved_download_host_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            item = dict(self.item(), url="https://untrusted.example/package.whl")
            with self.assertRaisesRegex(ValueError, "approved"):
                bootstrap.acquire(item, Path(temp), Path(temp))

    def test_download_retries_and_atomically_publishes_verified_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with patch.object(bootstrap, "urlopen", side_effect=[OSError("network"), io.BytesIO(b"verified")]) as request, patch.object(bootstrap.time, "sleep"):
                result = bootstrap.acquire(self.item(), path, path)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(result.read_bytes(), b"verified")
            self.assertFalse((path / "package.whl.partial").exists())

    def test_corrupt_remote_download_is_never_published(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with patch.object(bootstrap, "urlopen", side_effect=lambda *a, **k: io.BytesIO(b"badbytes")), patch.object(bootstrap.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    bootstrap.acquire(self.item(), path, path)
            self.assertFalse((path / "package.whl").exists())
            self.assertFalse((path / "package.whl.partial").exists())

    def test_http_500_retries_then_reuses_successful_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            error = HTTPError(self.item()["url"], 500, "Internal Server Error", {}, None)
            with patch.object(bootstrap, "urlopen", side_effect=[error, error, io.BytesIO(b"verified")]), patch.object(bootstrap.time, "sleep") as sleep:
                self.assertEqual(bootstrap.acquire(self.item(), path, path).read_bytes(), b"verified")
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])
            with patch.object(bootstrap, "urlopen", side_effect=AssertionError("network")):
                self.assertEqual(bootstrap.acquire(self.item(), path, path), path / "package.whl")

    def test_http_500_exhaustion_explains_recovery_and_cleans_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "package.whl.partial").write_bytes(b"unfinished")
            error = HTTPError(self.item()["url"], 500, "Internal Server Error", {}, None)
            with patch.object(bootstrap, "urlopen", side_effect=error) as request, patch.object(bootstrap.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "package.whl.*files.pythonhosted.org.*3 attempts.*500.*Retry setup later"):
                    bootstrap.acquire(self.item(), path, path)
            self.assertEqual(request.call_count, 3)
            self.assertFalse((path / "package.whl").exists())
            self.assertFalse((path / "package.whl.partial").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows bootstrap uses msvcrt locking")
    def test_concurrent_setup_waits_then_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("msvcrt.locking", side_effect=[OSError("busy"), None, None]) as locking, patch.object(bootstrap.time, "sleep") as sleep:
                with bootstrap.setup_lock(Path(temp)):
                    pass
            self.assertEqual(locking.call_count, 3)
            sleep.assert_called_once_with(1)

    def test_bundled_wheel_is_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "package.whl").write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                bootstrap.acquire(dict(self.item(), bundled="package.whl"), path, path)

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with zipfile.ZipFile(path / "bad.zip", "w") as archive:
                archive.writestr("../outside", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                bootstrap.safe_extract(path / "bad.zip", path / "output")

    def test_groups_keep_qt_and_cuda_out_of_cli_cpu(self):
        manifest = {"packages": [{"group": group} for group in ("core", "cpu", "cuda", "gui", "installer")]}
        self.assertEqual([x["group"] for x in bootstrap.selected_packages(manifest, "CLI", "cpu")], ["core", "cpu", "installer"])
        self.assertEqual([x["group"] for x in bootstrap.selected_packages(manifest, "GUI", "cuda")], ["core", "cuda", "gui", "installer"])

    def test_receipt_checks_files_and_detects_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(bootstrap.valid_runtime(root))
            file = root / "test.dll"
            file.write_bytes(b"verified")
            (root / "receipt.json").write_text(json.dumps({"files": [dict(self.item(), path="test.dll")]}))
            self.assertTrue(bootstrap.valid_runtime(root, True))
            file.write_bytes(b"badbytes")
            self.assertFalse(bootstrap.valid_runtime(root, True))

    def test_environment_does_not_inherit_host_python_or_dll_paths(self):
        with patch.dict("os.environ", {"PATH": "C:/untrusted/bin", "PYTHONPATH": "C:/untrusted", "PYTHONHOME": "C:/host"}):
            env = bootstrap.environment(Path("data"), Path("runtime"))
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("untrusted", env["PATH"])

    def test_environment_exposes_only_app_local_cuda_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            cublas = runtime / "site/nvidia/cublas/bin"
            cublas.mkdir(parents=True)
            with patch.dict("os.environ", {"PATH": "C:/untrusted/bin"}):
                env = bootstrap.environment(Path("data"), runtime)
            self.assertEqual(env["PATH"].split(bootstrap.os.pathsep)[0], str(cublas))
            self.assertNotIn("untrusted", env["PATH"])
            self.assertNotIn(str(runtime / "site/nvidia/cudnn/bin"), env["PATH"])

    def test_default_runtime_detects_gpu_without_importing_torch(self):
        driver = Mock()
        driver.cuInit.return_value = 0
        def device_count(pointer):
            pointer._obj.value = 1
            return 0
        driver.cuDeviceGetCount.side_effect = device_count
        with patch.object(bootstrap.sys, "platform", "win32"), patch.object(bootstrap.ctypes, "WinDLL", return_value=driver, create=True):
            self.assertEqual(bootstrap.default_runtime_device(), "cuda")
            driver.cuInit.return_value = 100
            self.assertEqual(bootstrap.default_runtime_device(), "cpu")

    def test_default_runtime_handles_absent_driver_and_non_windows(self):
        with patch.object(bootstrap.sys, "platform", "win32"), patch.object(bootstrap.ctypes, "WinDLL", side_effect=OSError, create=True):
            self.assertEqual(bootstrap.default_runtime_device(), "cpu")
        with patch.object(bootstrap.sys, "platform", "darwin"), patch.object(bootstrap.ctypes, "WinDLL", create=True) as load:
            self.assertEqual(bootstrap.default_runtime_device(), "cpu")
            load.assert_not_called()

    def test_failed_publication_restores_previous_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, stage = root / "runtime", root / "stage"
            runtime.mkdir()
            (runtime / "old").write_text("preserve")
            stage.mkdir()
            original = Path.rename
            def rename(path, target):
                if path == stage:
                    raise OSError("locked")
                return original(path, target)
            with patch.object(Path, "rename", rename), self.assertRaises(OSError):
                bootstrap.publish_runtime(stage, runtime)
            self.assertEqual((runtime / "old").read_text(), "preserve")
            self.assertTrue(stage.exists())


if __name__ == "__main__":
    unittest.main()

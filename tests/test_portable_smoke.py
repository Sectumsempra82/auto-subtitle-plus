from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from tools import portable_smoke


class PortableSmokeTests(unittest.TestCase):
    def test_smoke_environment_uses_minimal_path_and_clears_python_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = portable_smoke.smoke_environment(Path(tmp), None)

        self.assertEqual(env["PATH"], portable_smoke.minimal_windows_path())
        self.assertEqual(env["AUTO_SUBTITLE_PLUS_DATA_DIR"], str((Path(tmp) / "data").resolve()))
        self.assertEqual(env["USERPROFILE"], str(Path(tmp)))
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("HOME", env)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")

    def test_run_command_check_passes_when_expected_output_is_present(self):
        command = [sys.executable, "-c", "print('Auto Subtitle Plus usage: model local translation')"]
        result = portable_smoke.run_command_check("help", command, ("usage:", "local translation"), 10, None)

        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(result["returncode"], 0)

    def test_run_command_check_fails_on_missing_expected_output(self):
        command = [sys.executable, "-c", "print('nothing interesting')"]
        result = portable_smoke.run_command_check("help", command, ("usage:",), 10, None)

        self.assertEqual(result["status"], "failed")
        self.assertIn("missing expected output text", result["error"])

    def test_run_command_check_times_out_and_reports_spawned_tree_only(self):
        command = [sys.executable, "-c", "import time; time.sleep(60)"]
        result = portable_smoke.run_command_check("timeout", command, ("unused",), 0.2, None)

        self.assertEqual(result["status"], "failed")
        self.assertIn("terminated spawned process tree", result["error"])

    def test_main_writes_structured_report_for_fake_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_exe = Path(tmp) / "fake.exe"
            output_dir = Path(tmp) / "out"
            fake_exe.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "args = set(sys.argv[1:])\n"
                "if '--help' in args:\n"
                "    print('usage: Auto Subtitle Plus')\n"
                "elif '--list-models' in args:\n"
                "    print('model tiny')\n"
                "elif '--list-translation-models' in args:\n"
                "    print('opus-en-fr\\topus\\tinstalled\\ten->fr')\n"
                "else:\n"
                "    print('completed')\n",
                encoding="utf-8",
            )
            os.chmod(fake_exe, 0o755)

            exit_code = portable_smoke.main(["--cli", str(fake_exe), "--output-dir", str(output_dir)])

            report = json.loads((output_dir / "portable-smoke-report.json").read_text(encoding="utf-8"))

        expected_exit = 0 if sys.platform != "win32" else 1
        self.assertEqual(exit_code, expected_exit)
        self.assertEqual(report["checks"][-1]["status"], "skipped")

    def test_gui_uses_explicit_smoke_test_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            gui = Path(tmp) / "gui.exe"
            gui.write_bytes(b"fake")
            args = portable_smoke.build_parser().parse_args(
                ["--cli", str(gui), "--gui", str(gui), "--output-dir", str(Path(tmp) / "out")]
            )
            commands = []

            def fake_run(name, command, *_args, **_kwargs):
                commands.append((name, command))
                return {"name": name, "status": "passed"}

            with mock.patch.object(portable_smoke, "run_command_check", side_effect=fake_run):
                report = portable_smoke.run_smoke(args)

        self.assertEqual(report["status"], "failed")
        self.assertIn(("gui-portable-smoke", [str(gui.resolve()), "--smoke-test", "--portable-smoke", str(Path(tmp) / "out" / "gui-smoke-report.json"), "--smoke-output-dir", str(Path(tmp) / "out" / "gui-output")]), commands)

    def test_fixture_requires_explicit_asr_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "cli.exe"
            fixture = Path(tmp) / "clip.wav"
            cli.write_bytes(b"fake")
            fixture.write_bytes(b"fake")
            args = portable_smoke.build_parser().parse_args(
                ["--cli", str(cli), "--output-dir", str(Path(tmp) / "out"), "--fixture", str(fixture)]
            )

            result = portable_smoke.run_fixture_check(args, cli, Path(tmp) / "out", None)

        self.assertEqual(result["status"], "failed")
        self.assertIn("--asr-model is required", result["error"])

    def test_run_smoke_resolves_relative_output_dir_before_fixture_command(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "cli.exe"
            fixture = root / "clip.wav"
            cli.write_bytes(b"fake")
            fixture.write_bytes(b"fake")
            commands = []

            def fake_run(name, command, *_args, **_kwargs):
                commands.append((name, command))
                if name == "fixture-asr-translation":
                    output_dir = Path(command[command.index("--output-dir") + 1])
                    self.assertTrue(output_dir.is_absolute())
                    (output_dir / "clip.srt").parent.mkdir(parents=True, exist_ok=True)
                    (output_dir / "clip.srt").write_text("ok", encoding="utf-8")
                    (output_dir / "clip.txt").write_text("ok", encoding="utf-8")
                return {"name": name, "status": "passed"}

            try:
                os.chdir(root)
                args = portable_smoke.build_parser().parse_args(
                    ["--cli", str(cli), "--output-dir", "relative-out", "--fixture", str(fixture), "--asr-model", "turbo"]
                )
                with mock.patch.object(portable_smoke, "run_command_check", side_effect=fake_run):
                    report = portable_smoke.run_smoke(args)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["output_dir"], str((root / "relative-out").resolve()))


if __name__ == "__main__":
    unittest.main()

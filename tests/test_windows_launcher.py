import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "Native Windows launcher lifecycle")
class WindowsLauncherTests(unittest.TestCase):
    def test_output_during_handle_recreation_and_normal_exit(self):
        compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            executable = folder / "harness.exe"
            (folder / "Start-Application.ps1").write_text(
                "[Console]::WriteLine('ASP_READY')\n"
                "for ($index=0; $index -lt 1000; $index++) {\n"
                "[Console]::WriteLine('output ' + $index)\n"
                "[Console]::Error.WriteLine('error ' + $index)\n"
                "Start-Sleep -Milliseconds 2\n}\nexit 0\n",
                encoding="utf-8",
            )
            subprocess.run([
                str(compiler), "/nologo", "/define:GUI", "/target:winexe", "/platform:x64",
                "/r:System.Web.Extensions.dll", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
                "/main:Harness", "/out:" + str(executable), str(ROOT / "packaging/Launcher.cs"),
                str(ROOT / "tests/fixtures/launcher_lifecycle.cs"),
            ], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], timeout=25, capture_output=True, text=True)
            error = folder / "race-error.txt"
            self.assertEqual(result.returncode, 0, error.read_text() if error.exists() else result.stderr)
            self.assertFalse(error.exists())


if __name__ == "__main__":
    unittest.main()

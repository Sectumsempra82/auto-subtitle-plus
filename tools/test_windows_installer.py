"""Exercise real Windows installers in disposable folders; never use a daily installation."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import winreg

APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\local.autosubtitleplus.desktop_is1'


def registered_folder() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY, access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, 'InstallLocation')[0]
    except FileNotFoundError:
        return None


def run(installer: Path, log: Path, folder: Path | None = None, succeeds: bool = True) -> None:
    command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/NOICONS', '/TASKS=', '/LOG=' + str(log)]
    if folder is not None:
        command.append('/DIR=' + str(folder))
    result = subprocess.run(command, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    if (result.returncode == 0) != succeeds:
        raise AssertionError(f'Unexpected installer exit {result.returncode}; see {log}')


def verify_payload(folder: Path) -> None:
    manifest = json.loads((folder / 'manifest.json').read_text())
    assert manifest['edition'] == 'GUI+CLI'
    for item in manifest['files']:
        path = folder / item['path']
        assert path.stat().st_size == item['size'], path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--older-installer', type=Path, required=True)
    parser.add_argument('--portable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-data', type=Path, help='Existing disposable test runtime cache for installed-app smoke')
    args = parser.parse_args()
    if registered_folder():
        raise SystemExit('A real installation is registered. Use an isolated Windows user for this test.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    with tempfile.TemporaryDirectory(prefix='install test ', dir=output) as temporary:
        root = Path(temporary).resolve()
        install = root / 'Application with spaces'
        try:
            install.mkdir()
            sentinel = install / 'keep.txt'
            sentinel.write_bytes(b'not an application')
            run(args.installer.resolve(), output / 'unknown-folder.log', install, False)
            assert sentinel.read_bytes() == b'not an application'
            sentinel.unlink()
            checks.append('unrelated nonempty folder rejected')

            shutil.copytree(args.portable.resolve(), install, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('data'))
            data = install / 'data'
            preserved = {
                'AutoSubtitlePlus/desktop/state.json': b'{"settings":{"model":"small"},"queue":[]}',
                'huggingface/hub/model-fixture.bin': b'model fixture',
                'downloads/cached-fixture.whl': b'verified archive fixture',
                'runtimes/fixture/receipt.json': b'{"preserve":true}',
                'runtime-profile.json': b'{"device":"cpu"}',
            }
            for name, content in preserved.items():
                target = data / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            exported = install / 'example.txt'
            exported.write_bytes(b'user transcript')
            stale = install / 'app/auto_subtitle_plus/obsolete_module.py'
            stale.write_text('obsolete = True')

            mutex = kernel.CreateMutexW(None, False, 'AutoSubtitlePlus.Running')
            try:
                run(args.installer.resolve(), output / 'running-app.log', install, False)
                assert stale.exists()
            finally:
                kernel.CloseHandle(mutex)
            checks.append('running application blocks update without changing code')

            kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                          ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
            kernel.CreateFileW.restype = ctypes.c_void_p
            locked = kernel.CreateFileW(str(install / 'auto_subtitle_plus_gui.exe'), 0x80000000, 1, None, 3, 0, None)
            assert locked != ctypes.c_void_p(-1).value
            try:
                run(args.installer.resolve(), output / 'locked-legacy-launcher.log', install, False)
                assert stale.exists()
            finally:
                kernel.CloseHandle(locked)
            checks.append('legacy launcher file lock blocks update before deleting code')

            run(args.older_installer.resolve(), output / 'zip-to-installer.log', install)
            verify_payload(install)
            assert not stale.exists()
            checks.append('portable ZIP upgraded in place; obsolete Python module removed')
            stale.write_text('obsolete = True')
            # No /DIR: the new version must reuse the installer registration.
            run(args.installer.resolve(), output / 'upgrade.log')
            verify_payload(install)
            assert not stale.exists()
            assert Path(registered_folder()).resolve() == install
            checks.append('newer installer reuses registered location and replaces payload')
            run(args.installer.resolve(), output / 'reinstall.log')
            verify_payload(install)
            checks.append('same-version reinstall succeeds')
            before = (install / 'installation.ini').read_bytes()
            run(args.older_installer.resolve(), output / 'downgrade.log', succeeds=False)
            assert (install / 'installation.ini').read_bytes() == before
            verify_payload(install)
            checks.append('downgrade rejected without modifying installed version')
            assert all((data / name).read_bytes() == content for name, content in preserved.items())
            assert exported.read_bytes() == b'user transcript'
            checks.append('settings, queue, models, runtimes, downloads and export preserved byte-for-byte')
            run(install / 'unins000.exe', output / 'uninstall.log')
            assert registered_folder() is None
            assert not (install / 'auto_subtitle_plus_gui.exe').exists()
            assert all((data / name).read_bytes() == content for name, content in preserved.items())
            assert exported.read_bytes() == b'user transcript'
            checks.append('uninstall removes app and registration while preserving user data and export')

            install = root / 'Fresh installation'
            run(args.installer.resolve(), output / 'fresh-install.log', install)
            verify_payload(install)
            assert not (install / 'data').exists()
            checks.append('fresh install succeeds without downloading or starting dependencies')
            if args.runtime_data:
                environment = dict(os.environ)
                environment.pop('PYTHONPATH', None)
                environment.pop('PYTHONHOME', None)
                environment.update({
                    'AUTO_SUBTITLE_PLUS_DATA_DIR': str(args.runtime_data.resolve()),
                    'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                    'QT_QPA_PLATFORM': 'offscreen',
                    'QT_QPA_FONTDIR': str(Path(os.environ['SystemRoot']) / 'Fonts'),
                    'PATH': os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']]),
                })
                result = subprocess.run([str(install / 'auto_subtitle_plus.exe'), '--offline', '--help'],
                                        env=environment, text=True, capture_output=True, timeout=120,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                (output / 'installed-cli.log').write_text(result.stdout + result.stderr)
                assert result.returncode == 0 and '--output-txt' in result.stdout
                report = output / 'installed-gui.json'
                result = subprocess.run([str(install / 'auto_subtitle_plus_gui.exe'),
                                         '--portable-smoke', str(report), '--smoke-timeout-ms', '20000'],
                                        env=environment, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
                assert result.returncode == 0 and json.loads(report.read_text())['status'] == 'passed'
                checks.append('installed CLI and GUI start offline with cached dependencies and minimal PATH')
        finally:
            registered = registered_folder()
            if registered:
                registered_path = Path(registered).resolve()
                if not registered_path.is_relative_to(root):
                    raise RuntimeError('Unexpected installation path; refusing cleanup')
                run(registered_path / 'unins000.exe', output / 'cleanup.log')
    (output / 'report.json').write_text(json.dumps({'status': 'passed', 'checks': checks}, indent=2))
    print(json.dumps({'status': 'passed', 'checks': checks}, indent=2))


if __name__ == '__main__':
    main()

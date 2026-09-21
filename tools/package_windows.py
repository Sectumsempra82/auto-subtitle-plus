"""Package the verified lightweight GUI and CLI payloads as a per-user installer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from tools.build_bootstrap import ROOT, digest


def verify_payload(folder: Path) -> dict:
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    expected = {'manifest.json'}
    for item in manifest['files']:
        relative = Path(item['path'])
        path = folder / relative
        if relative.anchor or '..' in relative.parts or relative.parts[0].lower() == 'data':
            raise ValueError(f'Invalid payload path: {relative}')
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError(f'Payload link escapes folder: {relative}')
        if path.stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise ValueError(f'Payload differs from manifest: {relative}')
        expected.add(relative.as_posix())
    actual = {path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file()}
    if actual != expected:
        raise ValueError('Unexpected files in payload; rebuild into a clean output folder')
    return manifest


def build_installer(output: Path, compiler: Path) -> Path:
    gui = output / 'AutoSubtitlePlus-GUI'
    cli = output / 'AutoSubtitlePlus-CLI'
    manifest = verify_payload(gui)
    if verify_payload(cli)['version'] != manifest['version']:
        raise ValueError('GUI and CLI versions differ')
    version = manifest['version']
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)rc(\d+)', version)
    if not match:
        raise ValueError('Define a Windows version mapping before changing the release version format')
    file_version = '.'.join(match.groups())
    with tempfile.TemporaryDirectory(prefix='asp-windows-installer-') as temporary:
        payload = Path(temporary) / 'payload'
        shutil.copytree(gui, payload)
        shutil.copy2(cli / 'auto_subtitle_plus.exe', payload)
        shutil.copy2(ROOT / 'packaging/WINDOWS-INSTALLER.txt', payload)
        manifest['edition'] = 'GUI+CLI'
        manifest['files'] = [
            {'path': path.relative_to(payload).as_posix(), 'size': path.stat().st_size, 'sha256': digest(path)}
            for path in sorted(payload.rglob('*')) if path.is_file() and path.name != 'manifest.json'
        ]
        (payload / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        subprocess.run([
            str(compiler), '/Qp', '/DAppVersion=' + version, '/DFileVersion=' + file_version,
            '/DPayloadDir=' + str(payload), '/O' + str(output), str(ROOT / 'packaging/windows-installer.iss'),
        ], check=True)
    installer = output / 'AutoSubtitlePlus-Setup-Windows-x64.exe'
    installer.with_suffix('.exe.sha256').write_text(digest(installer) + '  ' + installer.name + '\n', encoding='ascii')
    return installer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/windows-light')
    parser.add_argument('--compiler', type=Path, required=True, help='Inno Setup 6.7.3 ISCC.exe')
    args = parser.parse_args()
    print(build_installer(args.output.resolve(), args.compiler.resolve()))


if __name__ == '__main__':
    main()

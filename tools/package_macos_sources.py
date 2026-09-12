"""Collect pinned native-library sources alongside the Mac binary release."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = json.loads((ROOT / 'packaging/macos-sources.json').read_text())
    directory = ROOT / '.build/macos-source-archives'
    directory.mkdir(parents=True, exist_ok=True)

    def download(row):
        suffix = '.tar.xz' if row['url'].endswith('.xz') else '.tar.bz2' if row['url'].endswith('.bz2') else '.tar.gz'
        target = directory / (row['name'] + suffix)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + '.part')
            with urllib.request.urlopen(row['url'], timeout=60) as response, temporary.open('wb') as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
            temporary.replace(target)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if row.get('sha256') and row['sha256'] != digest:
            raise ValueError(f"Source checksum mismatch: {row['name']}")
        row['sha256'] = digest
        row['file'] = target.name
        with tarfile.open(target) as archive:
            if not archive.getmembers():
                raise ValueError(f"Empty source archive: {target.name}")
        print(f"Verified {row['name']}", flush=True)
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        verified = list(pool.map(download, rows))
    (directory / 'sources.json').write_text(json.dumps(verified, indent=2) + '\n')
    archive_path = ROOT / 'dist/AutoSubtitlePlus-macOS-NativeSources.tar'
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, 'w') as archive:
        for row in verified:
            archive.add(directory / row['file'], arcname=row['file'])
        archive.add(directory / 'sources.json', arcname='sources.json')
    with archive_path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    archive_path.with_suffix('.tar.sha256').write_text(f'{digest}  {archive_path.name}\n')
    print(archive_path)


if __name__ == '__main__':
    main()

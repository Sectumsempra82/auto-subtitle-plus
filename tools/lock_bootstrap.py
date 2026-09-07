"""Refresh the reviewed Windows dependency catalog from official package metadata."""
from __future__ import annotations

import argparse
import hashlib
import html.parser
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
TAGS = list(sys_tags())


def read_json(url):
    with urlopen(url, timeout=60) as response:
        return json.load(response)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pypi_wheel(name, version, pip_python):
    info = read_json(f"https://pypi.org/pypi/{name}/{version}/json")
    choices = []
    for item in info["urls"]:
        if item["filename"].endswith(".whl"):
            tags = parse_wheel_filename(item["filename"])[3]
            ranks = [TAGS.index(tag) for tag in tags if tag in TAGS]
            if ranks:
                choices.append((min(ranks), item))
    if choices:
        item = min(choices, key=lambda pair: pair[0])[1]
        entry = {"filename": item["filename"], "url": item["url"],
                 "size": item["size"], "sha256": item["digests"]["sha256"]}
    else:
        source = next(item for item in info["urls"] if item["packagetype"] == "sdist")
        vendor = ROOT / "packaging" / "vendor"
        vendor.mkdir(exist_ok=True)
        env = dict(os.environ, SOURCE_DATE_EPOCH="1788739200")
        subprocess.run([pip_python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                        "--wheel-dir", str(vendor), source["url"] + "#sha256=" + source["digests"]["sha256"]],
                       check=True, env=env)
        wheel = next(path for path in vendor.glob("*.whl")
                     if canonicalize_name(parse_wheel_filename(path.name)[0]) == canonicalize_name(name))
        entry = {"filename": wheel.name, "bundled": "vendor/" + wheel.name,
                 "size": wheel.stat().st_size, "sha256": digest(wheel),
                 "source_url": source["url"], "source_sha256": source["digests"]["sha256"],
                 "reason": "No compatible prebuilt wheel published by this project"}
    entry.update(name=name, version=version, project_url=f"https://pypi.org/project/{name}/{version}/",
                 license=info["info"].get("license_expression") or info["info"].get("license") or "See package license")
    return entry


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


def torch_wheel(name, version, device):
    parser = Links()
    with urlopen(f"https://download.pytorch.org/whl/{device}/{name}/", timeout=60) as response:
        parser.feed(response.read().decode())
    for url in parser.links:
        filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        if filename == f"{name}-{version}+{device}-cp313-cp313-win_amd64.whl":
            url = url.replace("https://download-r2.pytorch.org/", "https://download.pytorch.org/")
            with urlopen(Request(url.split("#")[0], method="HEAD"), timeout=60) as response:
                size = int(response.headers["Content-Length"])
            return {"name": name, "version": version + "+" + device, "filename": filename,
                    "url": url.split("#")[0], "sha256": url.split("#sha256=")[1], "size": size,
                    "license": "BSD-3-Clause; see wheel notices", "project_url": "https://pytorch.org/"}
    raise RuntimeError(f"Official {name} {device} wheel not found")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pip-python", required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info[:2] != (3, 13):
        raise SystemExit("Lock on Windows x64 Python 3.13, using the tested application environment")
    skip = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes"}
    pending = []
    for line in (ROOT / "packaging/requirements-windows.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            req = Requirement(line)
            if canonicalize_name(req.name) not in skip:
                pending.append(req.name)
    pending.extend(["torch", "torchaudio", "torchvision"])
    versions = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in versions:
            continue
        dist = metadata.distribution(name)
        versions[name] = dist.version
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                installed = metadata.version(req.name)
                if req.specifier and installed not in req.specifier:
                    raise RuntimeError(f"Installed dependency mismatch: {name} requires {req}")
                pending.append(req.name)
    entries = []
    for name, version in sorted(versions.items()):
        if name in {"torch", "torchaudio", "torchvision"}:
            for device in ("cpu", "cu118"):
                entry = torch_wheel(name, version.split("+")[0], device)
                entry["group"] = "cpu" if device == "cpu" else "cuda"
                entries.append(entry)
        else:
            print(f"Locking {name} {version}", flush=True)
            entry = pypi_wheel(name, version, args.pip_python)
            entry["group"] = ("gui" if name in {"pyside6-essentials", "shiboken6"}
                              else "cuda" if name.startswith("nvidia-") else "core")
            entries.append(entry)
    installer = pypi_wheel("installer", "0.7.0", args.pip_python)
    installer["group"] = "installer"
    entries.append(installer)
    python_url = "https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip"
    with urlopen(python_url, timeout=60) as response:
        python_bytes = response.read()
    manifest = {"schema": 1, "python": {"url": python_url, "filename": python_url.rsplit("/", 1)[-1],
                "size": len(python_bytes), "sha256": hashlib.sha256(python_bytes).hexdigest()},
                "ffmpeg": {"url": "https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip",
                "filename": "ffmpeg-8.0.1-essentials_build.zip", "size": 106259850,
                "sha256": "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673"}, "packages": entries}
    (ROOT / "packaging/dependencies-windows.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Locked {len(entries)} packages. Review catalog before release.")


if __name__ == "__main__":
    main()

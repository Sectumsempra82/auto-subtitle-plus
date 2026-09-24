#!/bin/zsh
set -euo pipefail

cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

usage() {
    cat <<'EOF'
Usage: ./Build\ Mac.command [--local | --release BUILD_NUMBER]

  --local                 Build a Mac app tied to this checkout and .venv (default).
  --release BUILD_NUMBER  Build the distributable app, ZIP, PKG, and source archive.

Use a new, increasing build number for every release build. The latest published
Mac build number documented in packaging/macos.md is 8.
EOF
}

mode="${1:---local}"
if [[ "$mode" == -h || "$mode" == --help ]]; then
    usage
    exit 0
fi

if [[ ! -x .venv/bin/python ]]; then
    print -u2 'Missing .venv/bin/python. Create the venv and install the GUI dependencies first; see packaging/macos.md.'
    exit 1
fi

case "$mode" in
    --local)
        if [[ $# -gt 1 ]]; then
            usage >&2
            exit 2
        fi
        .venv/bin/python tools/build_macos.py
        print 'Local app: dist/macos/Auto Subtitle Plus.app'
        ;;
    --release)
        if [[ $# -ne 2 ]]; then
            usage >&2
            exit 2
        fi
        if [[ ! "$2" =~ '^[1-9][0-9]*$' ]]; then
            print -u2 'BUILD_NUMBER must be a positive integer greater than prior published builds.'
            exit 2
        fi
        .venv/bin/python -c 'import PyInstaller' || {
            print -u2 'PyInstaller is missing. Install it with: .venv/bin/python -m pip install "pyinstaller==6.22.2"'
            exit 1
        }
        TMPDIR=/private/tmp QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
        .venv/bin/python tools/package_macos_sources.py
        .venv/bin/python tools/package_macos.py --build-number "$2"
        print 'Release app: dist/macos-release/Auto Subtitle Plus.app'
        print 'Installer and ZIP: dist/AutoSubtitlePlus-GUI-macOS-arm64.*'
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

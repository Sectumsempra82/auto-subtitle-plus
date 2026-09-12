#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
    print 'Create the project .venv and install .[gui,faster] first. See packaging/macos.md.'
    read '?Press Return to close.'
    exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec .venv/bin/python -m auto_subtitle_plus.desktop "$@"

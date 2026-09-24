"""Platform wording so one workspace reads naturally on every desktop.

The workspace layout is identical everywhere. Only the few strings that name
the machine, and the search shortcut, differ between macOS and Windows.
"""
from __future__ import annotations

import sys

#: How the workspace refers to the computer it is running on.
DEVICE = {"darwin": "Mac", "win32": "PC"}.get(sys.platform, "computer")

#: Shortcut that focuses the settings search field. Qt renders the portable
#: "Ctrl" sequence as Command on macOS, so one sequence fits both desktops.
SEARCH_SHORTCUT = "Ctrl+K"

#: Label for that shortcut in hover help, using each platform's own name.
SEARCH_SHORTCUT_LABEL = "Command-K" if sys.platform == "darwin" else "Ctrl+K"

#: Compute type is CPU-only on macOS; elsewhere it is a real GPU choice.
COMPUTE_TYPE_HINT = (
    "Not supported on this Mac." if sys.platform == "darwin"
    else "Faster backend only: numerical precision and memory use."
)

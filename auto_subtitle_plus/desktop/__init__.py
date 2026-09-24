"""Optional Qt desktop frontend. The processing library does not import Qt."""

import sys


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        if sys.platform == "darwin":
            from .macos import launch
            return launch()
        else:
            from .window import launch
            return launch()
    except ImportError as error:
        if error.name and error.name.startswith("PySide6"):
            raise SystemExit('Desktop support is not installed. Install auto_subtitle_plus[gui].') from error
        raise

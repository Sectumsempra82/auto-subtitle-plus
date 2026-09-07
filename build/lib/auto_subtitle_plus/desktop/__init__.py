"""Optional Qt desktop frontend. The processing library does not import Qt."""


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        from .window import launch
    except ImportError as error:
        if error.name and error.name.startswith("PySide6"):
            raise SystemExit('Desktop support is not installed. Install auto_subtitle_plus[gui].') from error
        raise
    return launch()

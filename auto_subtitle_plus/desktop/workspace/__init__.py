"""Shared desktop workspace frontend for Auto Subtitle Plus."""


def launch():
    """Show the shared workspace window.

    Importing `.shell` here (rather than at module load time) keeps Qt-free
    submodules of this package, such as `wording`, importable without PySide6
    installed: importing any submodule still runs this `__init__.py` first.
    """
    from .shell import launch as _launch
    return _launch()

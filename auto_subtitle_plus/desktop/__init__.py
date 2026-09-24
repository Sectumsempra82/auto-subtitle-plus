"""Optional Qt desktop frontend. The processing library does not import Qt."""

import sys

#: Platforms that show the redesigned workspace instead of the classic window.
WORKSPACE_PLATFORMS = ("darwin", "win32")


def uses_workspace() -> bool:
    """Whether this platform's app shows the workspace shell."""
    return sys.platform in WORKSPACE_PLATFORMS


def window_class():
    """The MainWindow subclass this platform's app actually shows."""
    if uses_workspace():
        from .workspace.shell import WorkspaceMainWindow
        return WorkspaceMainWindow
    from .window import MainWindow
    return MainWindow


def theme_application(app) -> None:
    """Apply the palette and stylesheet that go with this platform's window."""
    from .window import apply_theme
    apply_theme(app)
    if uses_workspace():
        # The workspace styles its own central widget; the classic app-wide
        # stylesheet would fight those rules.
        app.setStyleSheet("")


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        if uses_workspace():
            from .workspace import launch
            return launch()
        else:
            from .window import launch
            return launch()
    except ImportError as error:
        if error.name and error.name.startswith("PySide6"):
            raise SystemExit('Desktop support is not installed. Install auto_subtitle_plus[gui].') from error
        raise

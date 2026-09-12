"""Frozen Mac entry point; user data stays outside the application bundle."""
import multiprocessing
import os
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))

    from auto_subtitle_plus.portable import prepend_existing_paths
    prepend_existing_paths(["/opt/homebrew/bin", "/usr/local/bin"])

    if "--smoke-test" in sys.argv or "--portable-smoke" in sys.argv:
        from gui_smoke import main
        sys.exit(main(sys.argv[1:]))

    from auto_subtitle_plus.desktop import main
    sys.exit(main())

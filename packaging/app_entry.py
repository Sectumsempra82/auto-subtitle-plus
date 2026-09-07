import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from auto_subtitle_plus.portable import bootstrap
    bootstrap()
    edition = sys.argv.pop(1)
    if edition == "GUI":
        if "--smoke-test" in sys.argv or "--portable-smoke" in sys.argv:
            from gui_smoke import main
        else:
            from auto_subtitle_plus.desktop import main
    else:
        from auto_subtitle_plus.cli import main
    raise SystemExit(main())

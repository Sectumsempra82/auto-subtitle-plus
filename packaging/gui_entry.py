import multiprocessing
import os
from pathlib import Path
import sys
from auto_subtitle_plus.portable import bootstrap

if __name__ == "__main__":
    bootstrap()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    multiprocessing.freeze_support()
    if "--portable-smoke" in sys.argv or "--smoke-test" in sys.argv:
        try:
            from gui_smoke import main
        except ImportError:
            import importlib.util
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            smoke_path = bundle_root / "packaging" / "gui_smoke.py"
            spec = importlib.util.spec_from_file_location("auto_subtitle_plus_packaging_gui_smoke", smoke_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load GUI smoke helper: {smoke_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            main = module.main
        sys.exit(main(sys.argv[1:]))
    from auto_subtitle_plus.desktop import main
    sys.exit(main())

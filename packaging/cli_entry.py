import multiprocessing
from auto_subtitle_plus.portable import bootstrap

if __name__ == "__main__":
    bootstrap()
    multiprocessing.freeze_support()
    from auto_subtitle_plus.cli import main
    raise SystemExit(main())

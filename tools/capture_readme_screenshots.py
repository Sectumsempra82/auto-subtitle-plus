"""Render the real desktop with example filenames only; never run media jobs."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Example media, sized so the queue shows realistic figures without real files.
MEDIA = (("Interview-part-1.mp4", 486_000_000, "queued"),
         ("Lecture-recording.mkv", 1_240_000_000, "queued"),
         ("Podcast-ep-12.wav", 92_000_000, "completed"))

TRANSCRIPT = ("Welcome back to the show. Today we are talking about local speech recognition\n"
              "and what it takes to run these models on your own machine.\n")
SUBTITLES = ("1\n00:00:00,000 --> 00:00:03,120\nWelcome back to the show.\n\n"
             "2\n00:00:03,120 --> 00:00:07,400\nToday we are talking about local speech recognition.\n")


def sample_queue(root: Path):
    """Write empty example media and outputs, then describe them as queue items."""
    from auto_subtitle_plus.desktop.state import QueueItem

    items = []
    for name, size, status in MEDIA:
        path = root / name
        with path.open("wb") as handle:
            handle.truncate(size)
        items.append(QueueItem(str(path), status=status))

    stem = Path(MEDIA[-1][0]).stem
    outputs = []
    for name, text in ((f"{stem}.txt", TRANSCRIPT), (f"{stem}.source.en.txt", TRANSCRIPT),
                       (f"{stem}.srt", SUBTITLES)):
        (root / name).write_text(text, encoding="utf-8")
        outputs.append(str(root / name))
    items[-1].outputs = outputs
    items[-1].stage, items[-1].progress, items[-1].elapsed = "Completed", 1.0, 138.0
    return items


def main():
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication
    from auto_subtitle_plus.desktop import theme_application, uses_workspace, window_class
    from auto_subtitle_plus.desktop.state import StateStore

    if not uses_workspace():
        raise SystemExit("This platform shows the classic window; capture the workspace on macOS or Windows.")
    prefix = "windows" if sys.platform == "win32" else "macos"
    gallery = ROOT / "website/assets"
    readme = ROOT / "assets/screenshots"
    for directory in (gallery, readme):
        directory.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    app.setApplicationName("Auto Subtitle Plus")
    theme_application(app)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        window = window_class()(store=StateStore(root / "state.json"), monitor=False)
        # Lay the window out at full size without putting it on the user's screen.
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.resize(1400, 960)
        items = sample_queue(root)
        window.model.replace(items)
        window.update_controls()
        window.show()
        app.processEvents()
        window.settings.restore({"language": "en", "backend": "faster", "model": "large-v3",
                                 "translate_enabled": True, "translate_to": "it"})
        app.processEvents()

        window.table.selectRow(len(items) - 1)
        window._item_translation[items[-1].id] = True
        for page, name in (("Queue", "queue"), ("Speech", "speech"),
                           ("Translate", "translate"), ("History", "processing")):
            window._select_page(page)
            app.processEvents()
            app.processEvents()
            target = gallery / f"{prefix}-{name}.png"
            if not window.grab().save(str(target)):
                raise RuntimeError(f"Screenshot save failed: {target}")
            print(target)
            if name == "queue":
                copy = readme / f"desktop-{prefix}.png"
                copy.write_bytes(target.read_bytes())
                print(copy)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()

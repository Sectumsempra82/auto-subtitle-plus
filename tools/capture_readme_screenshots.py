"""Render the real desktop with example filenames only; never run media jobs."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from auto_subtitle_plus.desktop.state import QueueItem, StateStore
    from auto_subtitle_plus.desktop.window import MainWindow, apply_theme

    output = ROOT / "assets/screenshots"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    with tempfile.TemporaryDirectory() as temp:
        window = MainWindow(store=StateStore(Path(temp) / "state.json"), monitor=False)
        window.model.replace([QueueItem(path="C:/Example Media/" + name) for name in
                              ("Interview.mp4", "Lecture.mkv", "Voice memo.wav")])
        window.model.refresh()
        window.update_controls()
        window.show()
        app.processEvents()
        for filename, tab in (("desktop-queue.png", 0), ("desktop-translation.png", 1)):
            if tab == 1:
                window.settings.restore({"language": "en", "translate_enabled": True,
                                         "translate_to": "it", "translation_model": "hy-mt2-1.8b-q8"})
            window.settings.tabs.setCurrentIndex(tab)
            app.processEvents()
            if not window.grab().save(str(output / filename)):
                raise RuntimeError("Screenshot save failed")
            print(output / filename)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


if __name__ == "__main__":
    main()

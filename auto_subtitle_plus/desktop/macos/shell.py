"""Mac-only navigation around the existing desktop processing controls."""
from __future__ import annotations

import sys

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..window import DesktopApplication, MainWindow, apply_theme
from .forms import MacForms
from .help import HelpScreen
from .icons import nav_icon
from .processing import ProcessingScreen
from .queue import MacQueueView
from .theme import DARK_STYLE, LIGHT_STYLE


class MacMainWindow(MainWindow):
    """Keep MainWindow's queue lifecycle and present its controls in Mac pages."""

    def __init__(self, store=None, monitor: bool = True, runner=None) -> None:
        self._item_translation: dict[str, bool] = {}
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
            font.setPointSize(11)
            app.setFont(font)
            palette = QPalette()
            for role, color in (
                (QPalette.ColorRole.Window, "#f2f5ff"),
                (QPalette.ColorRole.WindowText, "#15213d"),
                (QPalette.ColorRole.Base, "#ffffff"),
                (QPalette.ColorRole.AlternateBase, "#f5f7ff"),
                (QPalette.ColorRole.Text, "#15213d"),
                (QPalette.ColorRole.Button, "#ffffff"),
                (QPalette.ColorRole.ButtonText, "#15213d"),
                (QPalette.ColorRole.Highlight, "#4c78f3"),
                (QPalette.ColorRole.HighlightedText, "#ffffff"),
                (QPalette.ColorRole.Light, "#ffffff"),
                (QPalette.ColorRole.Midlight, "#edf2f9"),
                (QPalette.ColorRole.Mid, "#aab8cf"),
                (QPalette.ColorRole.Dark, "#64748d"),
                (QPalette.ColorRole.Shadow, "#26365a"),
                (QPalette.ColorRole.ToolTipBase, "#ffffff"),
                (QPalette.ColorRole.ToolTipText, "#15213d"),
            ):
                palette.setColor(role, QColor(color))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8794ad"))
            palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8794ad"))
            app.setPalette(palette)
        super().__init__(store=store, monitor=monitor, runner=runner)

    def _build_ui(self) -> None:
        super()._build_ui()
        self.setMinimumSize(960, 660)
        self.resize(1100, 760)
        old_root = self.centralWidget()
        splitter = old_root.layout().itemAt(1).widget()
        queue_panel = splitter.widget(0)
        queue_panel.setParent(None)
        self.settings.setParent(None)
        self.resources.setParent(None)
        self.summary.setParent(None)
        self.details.setParent(None)
        self.setCentralWidget(None)
        old_root.deleteLater()

        root = QWidget()
        root.setObjectName("macRoot")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("macSidebar")
        sidebar.setFixedWidth(184)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(14, 54, 14, 18)
        nav.setSpacing(5)
        outer.addWidget(sidebar)

        main = QWidget()
        outer.addWidget(main, 1)
        column = QVBoxLayout(main)
        column.setContentsMargins(14, 14, 14, 12)
        column.setSpacing(9)
        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(3, 0, 3, 0)
        title = QLabel("Auto Subtitle Plus")
        title.setObjectName("appTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings…")
        self.search.setToolTip("Find a setting by name and open its section. Command-K focuses search.")
        self.search.setFixedWidth(215)
        self.search.returnPressed.connect(self._search_settings)
        header.addWidget(self.search)
        column.addWidget(self.header)

        self.pages = QStackedWidget()
        column.addWidget(self.pages, 1)
        self.processing = ProcessingScreen()
        self.processing.mount_details(self.details)
        self.queue_view = MacQueueView(self, queue_panel, self.resources, self.summary)
        self.forms = MacForms(self.settings)
        self.settings.hide()
        self.pages.addWidget(self.queue_view)
        self.pages.addWidget(self.forms)
        self.pages.addWidget(self.processing)
        self.pages.addWidget(HelpScreen())

        self.nav_buttons: dict[str, QPushButton] = {}
        for name in ("Queue", "Speech", "Translate", "History"):
            self._add_nav(nav, name)
        nav.addStretch(1)
        for name in ("Settings", "Help"):
            self._add_nav(nav, name)
        self._select_page("Queue")
        shortcut = self.menuBar().addAction("Search Settings")
        shortcut.setShortcut(QKeySequence("Meta+K"))
        shortcut.triggered.connect(self._focus_search)
        self.table.selectionModel().selectionChanged.connect(self._refresh_processing)
        QApplication.instance().paletteChanged.connect(lambda _: self._apply_workspace_style())
        self._apply_workspace_style()

    def _add_nav(self, layout: QVBoxLayout, name: str) -> None:
        button = QPushButton(f"  {name}")
        button.setObjectName("macNav")
        button.setAccessibleName(name)
        button.setIcon(nav_icon(name, 18))
        button.setIconSize(QSize(18, 18))
        button.setCheckable(True)
        button.setToolTip(f"Open {name.lower()} in the Mac workspace.")
        button.clicked.connect(lambda checked=False, page=name: self._select_page(page))
        layout.addWidget(button)
        self.nav_buttons[name] = button

    def _apply_workspace_style(self) -> None:
        dark = QApplication.palette().color(QPalette.ColorRole.Base).lightness() < 128
        self.centralWidget().setStyleSheet(DARK_STYLE if dark else LIGHT_STYLE)

    def _focus_search(self) -> None:
        if self.pages.currentIndex() == 0:
            self._select_page("Settings")
        self.search.setFocus()

    def _select_page(self, name: str) -> None:
        if name == "Queue":
            index = 0
        elif name in ("Speech", "Translate", "Settings"):
            index = 1
            {"Speech": self.forms.show_speech,
             "Translate": self.forms.show_translate,
             "Settings": self.forms.show_settings}[name]()
        elif name in ("History", "Processing"):
            index = 2
            self.processing.update_history(self.items)
            self._refresh_processing()
        else:
            index = 3
        self.pages.setCurrentIndex(index)
        self.header.setVisible(index != 0)
        active = "History" if index == 2 else name
        for label, button in self.nav_buttons.items():
            button.setChecked(label == active)

    def _search_settings(self) -> None:
        query = self.search.text().strip().casefold()
        if not query:
            return
        targets = [("Speech", self.forms.speech), ("Translate", self.forms.translate)]
        targets.extend((name, self.forms.settings_stack.widget(index))
                       for index, name in enumerate(("Layout", "Files", "System")))
        for name, page in targets:
            labels = (widget.text() for widget in page.findChildren(QLabel))
            if query in name.casefold() or any(query in label.casefold() for label in labels):
                self._select_page(name if name in ("Speech", "Translate") else "Settings")
                if name in ("Layout", "Files", "System"):
                    self.forms.show_settings(name)
                return
        self.search.setToolTip(f"No setting found for {self.search.text()!r}.")

    def _refresh_processing(self, *_: object) -> None:
        item = self.active_item
        if item is None:
            rows = self.selected_rows()
            item = self.items[rows[0]] if rows else None
        translating = (bool(self.batch_settings.get("translate_to")) if self.active_item is not None
                       else self._item_translation.get(item.id) if item is not None else None)
        self.processing.update_status(item, self.running, translating)

    def start_queue(self) -> None:
        super().start_queue()
        if self.running:
            self._select_page("Processing")

    def _job_finished(self) -> None:
        if self.active_item is not None:
            self._item_translation[self.active_item.id] = bool(self.batch_settings.get("translate_to"))
        super()._job_finished()

    def _update_progress(self) -> None:
        super()._update_progress()
        if hasattr(self, "processing"):
            self._refresh_processing()

    def update_controls(self) -> None:
        super().update_controls()
        if hasattr(self, "queue_view"):
            self.queue_view.refresh(self.items)
            self.forms.setEnabled(not self.running and self.cache_worker is None)
            queued = sum(item.status == "queued" for item in self.items)
            self.nav_buttons["Queue"].setText(f"  Queue  {queued}" if queued else "  Queue")


def launch() -> int:
    app = QApplication.instance() or DesktopApplication(sys.argv)
    app.setApplicationName("Auto Subtitle Plus")
    app.setOrganizationName("AutoSubtitlePlus")
    apply_theme(app)
    window = MacMainWindow()
    if isinstance(app, DesktopApplication):
        app.window = window
        if app.pending_files:
            window.add_paths(app.pending_files)
            app.pending_files.clear()
    window.show()
    if len(sys.argv) > 1:
        window.add_paths(sys.argv[1:])
    return app.exec()

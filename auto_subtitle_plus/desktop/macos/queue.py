"""Queue page layout for the Mac workspace, reusing MainWindow controls."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import action_icon, nav_icon


class MacQueueView(QWidget):
    """Arrange the existing queue widgets to match the Mac Queue mockup.

    The queue panel's model, table, and action buttons are kept intact. This
    class changes their layout only; MainWindow remains responsible for queue
    behavior and state updates.
    """

    def __init__(self, window, queue_panel: QWidget, resources: QWidget, summary: QLabel) -> None:
        super().__init__()
        self.window = window
        self.setObjectName("macQueuePage")

        # Keep the queue stack itself so MainWindow.update_controls() can keep
        # using it. Its empty-state page becomes the same table, which leaves
        # the headers visible when there are no files.
        self._discard_empty_state()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        heading = QWidget()
        heading.setObjectName("macQueueHeader")
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(2, 0, 2, 0)
        heading_layout.setSpacing(8)
        title_block = QVBoxLayout()
        title_block.setSpacing(1)
        title = QLabel("Auto Subtitle Plus")
        title.setObjectName("appTitle")
        title_block.addWidget(title)
        subtitle = QLabel("Local speech-to-text and subtitle translation")
        subtitle.setObjectName("muted")
        title_block.addWidget(subtitle)
        heading_layout.addLayout(title_block)
        heading_layout.addStretch(1)
        summary.setObjectName("macQueueSummary")
        heading_layout.addWidget(summary, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(heading)

        toolbar = QWidget()
        toolbar.setObjectName("macQueueToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(7)
        window.add_button.setObjectName("macToolbarButton")
        window.add_button.setIcon(action_icon("add", 16, "#ffffff"))
        window.add_button.setIconSize(QSize(16, 16))
        window.add_button.setMinimumHeight(32)
        for button in (
            window.add_button,
            window.folder_button,
        ):
            if button is window.folder_button:
                button.setObjectName("macIconButton")
                button.setIcon(action_icon("folder"))
                button.setIconSize(QSize(16, 16))
                button.setFixedSize(32, 32)
            toolbar_layout.addWidget(button)
        toolbar_layout.addStretch(1)
        for button in (
            window.up_button,
            window.down_button,
            window.retry_button,
            window.remove_button,
            window.clear_button,
        ):
            button.setObjectName("macIconButton")
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(32, 32)
            toolbar_layout.addWidget(button)
        root.addWidget(toolbar)

        table_card = QFrame()
        table_card.setObjectName("macCard")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(window.queue_stack)
        window.table.setMinimumHeight(150)
        window.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(table_card, 1)

        self.drop_zone = QPushButton("Drop media files here\nVideo or audio files · Supports mp4, mkv, mov, wav and more")
        self.drop_zone.setObjectName("macDropZone")
        self.drop_zone.setToolTip("Add local audio or video. Drag files onto the window, or click to browse.")
        self.drop_zone.setMinimumHeight(68)
        self.drop_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.drop_zone.clicked.connect(window.choose_files)
        root.addWidget(self.drop_zone)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)
        window.start_button.setObjectName("primary")
        window.start_button.setIconSize(QSize(16, 16))
        window.start_button.setMinimumHeight(34)
        window.pause_button.setObjectName("secondary")
        window.pause_button.setIconSize(QSize(16, 16))
        window.pause_button.setMinimumHeight(34)
        window.cancel_button.setObjectName("macIconButton")
        window.cancel_button.setIconSize(QSize(16, 16))
        window.cancel_button.setFixedSize(32, 32)
        controls_layout.addWidget(window.start_button)
        controls_layout.addWidget(window.pause_button)
        controls_layout.addWidget(window.cancel_button)
        controls_layout.addStretch(1)
        self.queue_settings_button = QPushButton("Queue settings…")
        self.queue_settings_button.setObjectName("secondary")
        self.queue_settings_button.setIcon(nav_icon("settings"))
        self.queue_settings_button.setIconSize(QSize(16, 16))
        self.queue_settings_button.setMinimumHeight(34)
        self.queue_settings_button.setToolTip("Open output, layout, and processing settings before starting the queue.")
        self.queue_settings_button.clicked.connect(lambda: window._select_page("Settings"))
        controls_layout.addWidget(self.queue_settings_button)
        root.addWidget(controls)

        resources.setMinimumHeight(72)
        root.addWidget(resources)

        # Retain the unused legacy stage widgets as hidden children. The
        # details panel is mounted separately by ProcessingScreen.
        queue_panel.hide()
        queue_panel.setParent(self)
        window.current_label.hide()
        window.current_progress.hide()
        window.open_button.hide()

    def _discard_empty_state(self) -> None:
        stack = self.window.queue_stack
        table = self.window.table
        for index in range(stack.count()):
            widget = stack.widget(index)
            if widget is not table:
                stack.removeWidget(widget)
                widget.deleteLater()
        stack.setCurrentWidget(table)

    def refresh(self, items) -> None:
        """Keep the table visible for both populated and empty queues."""
        self.window.queue_stack.setCurrentWidget(self.window.table)

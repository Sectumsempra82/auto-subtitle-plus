"""Live queue status and output review for the desktop workspace."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QHBoxLayout, QLabel, QPlainTextEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QStyle, QTabWidget,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
    QFileIconProvider,
)

from ..state import QueueItem
from .icons import heading_icon
from .wording import DEVICE


class ProcessingScreen(QWidget):
    """A processing summary followed by the saved output review."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        page_heading = QHBoxLayout()
        heading_glyph = QLabel()
        heading_glyph.setPixmap(heading_icon("history").pixmap(QSize(28, 28)))
        heading_glyph.setAccessibleName("Processing icon")
        page_heading.addWidget(heading_glyph, alignment=Qt.AlignmentFlag.AlignTop)
        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(2)
        self.title = QLabel("Processing")
        self.title.setObjectName("workspacePageTitle")
        heading_copy.addWidget(self.title)
        self.description = QLabel(f"Transcribing and translating files. This runs locally on your {DEVICE}.")
        self.description.setObjectName("workspacePageSubtitle")
        heading_copy.addWidget(self.description)
        page_heading.addLayout(heading_copy)
        page_heading.addStretch(1)
        layout.addLayout(page_heading)

        # Summary card mirrors the mockup's media, stage, timing, and progress hierarchy.
        self.status_card = QFrame()
        self.status_card.setObjectName("workspaceStatusCard")
        status_layout = QVBoxLayout(self.status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(8)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        self.media_icon = QLabel()
        self.media_icon.setObjectName("workspaceMediaThumbnail")
        self.media_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.media_icon.setFixedSize(64, 64)
        self.media_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon).pixmap(48, 48))
        summary.addWidget(self.media_icon)
        file_copy = QVBoxLayout()
        file_copy.setSpacing(3)
        self.file_label = QLabel("No file is currently processing.")
        self.file_label.setObjectName("processingFile")
        self.file_label.setWordWrap(True)
        file_copy.addWidget(self.file_label)
        self.stage_label = QLabel("Select a queue item to review its saved outputs.")
        self.stage_label.setObjectName("muted")
        self.stage_label.setWordWrap(True)
        file_copy.addWidget(self.stage_label)
        summary.addLayout(file_copy, 1)
        self.timing = QVBoxLayout()
        self.timing.setSpacing(3)
        self.elapsed_label = QLabel("Elapsed   —")
        self.remaining_label = QLabel("Remaining   —")
        for label in (self.elapsed_label, self.remaining_label):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setObjectName("muted")
            self.timing.addWidget(label)
        summary.addLayout(self.timing)
        status_layout.addLayout(summary)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setToolTip("Progress for the current processing stage. It may reset when a new stage begins.")
        progress_row.addWidget(self.progress, 1)
        self.progress_value = QLabel("0%")
        self.progress_value.setObjectName("progressValue")
        self.progress_value.setMinimumWidth(34)
        self.progress_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.progress_value)
        status_layout.addLayout(progress_row)
        layout.addWidget(self.status_card)

        review_card = QFrame()
        review_card.setObjectName("workspaceCard")
        review_layout = QVBoxLayout(review_card)
        review_layout.setContentsMargins(8, 4, 8, 8)
        review_layout.setSpacing(0)
        self.preview = QTabWidget()
        self.preview.setDocumentMode(True)
        self.preview.setObjectName("processingReview")
        self.text_preview = QPlainTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setToolTip("Read-only preview of the original transcript when saved. Open the output in your editor to make changes.")
        self.translation_preview = QPlainTextEdit()
        self.translation_preview.setReadOnly(True)
        self.translation_preview.setToolTip("Read-only preview of the final translated text when translation was enabled.")
        self.subtitle_preview = QPlainTextEdit()
        self.subtitle_preview.setReadOnly(True)
        self.subtitle_preview.setToolTip("Read-only preview of the generated SRT or VTT subtitle file.")
        self.subtitle_rows = QTableWidget(0, 2)
        self.subtitle_rows.setHorizontalHeaderLabels(("Time", "Text"))
        self.subtitle_rows.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.subtitle_rows.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.subtitle_rows.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.subtitle_rows.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.subtitle_rows.setToolTip("Read-only cue preview from the generated SRT or VTT file. Open Files for the complete output.")
        self.preview.addTab(self.text_preview, "Transcript")
        self.preview.addTab(self.translation_preview, "Translation")
        self.preview.addTab(self.subtitle_rows, "Subtitles")
        self.preview.currentChanged.connect(self._sync_subtitle_view)
        review_layout.addWidget(self.preview, 1)
        layout.addWidget(review_card, 1)

        self.files_card = QFrame()
        self.files_card.setObjectName("workspaceCompletedCard")
        files_layout = QVBoxLayout(self.files_card)
        files_layout.setContentsMargins(12, 8, 12, 8)
        files_layout.setSpacing(6)
        files_header = QHBoxLayout()
        self.completed_label = QLabel("✓   Completed files (0)")
        self.completed_label.setObjectName("completedTitle")
        files_header.addWidget(self.completed_label, 1)
        self.completed_toggle = QToolButton()
        self.completed_toggle.setText("Show files  ▾")
        self.completed_toggle.setCheckable(True)
        self.completed_toggle.setToolTip("Expand or collapse the completed queue items.")
        self.completed_toggle.toggled.connect(self._toggle_completed)
        files_header.addWidget(self.completed_toggle)
        self.files_button = QPushButton("View files")
        self.files_button.setObjectName("secondary")
        self.files_button.setToolTip("Show generated files for the selected queue item.")
        self.files_button.clicked.connect(lambda: self._select_tab("Files"))
        files_header.addWidget(self.files_button)
        files_layout.addLayout(files_header)
        self.completed_items = QListWidget()
        self.completed_items.setObjectName("completedItems")
        self.completed_items.setMaximumHeight(112)
        self.completed_items.hide()
        files_layout.addWidget(self.completed_items)
        layout.addWidget(self.files_card)

        self._preview_key: tuple[tuple[str, ...], bool | None] | None = None
        self.details: QWidget | None = None
        self._outputs: QWidget | None = None
        self._completed_count = 0
        self._history_loaded = False

    def update_history(self, items: list[QueueItem]) -> None:
        """Show completed queue items without loading or deriving media content."""
        completed = [item for item in items if item.status == "completed"]
        self._history_loaded = True
        self._completed_count = len(completed)
        self.completed_label.setText(f"✓   Completed files ({self._completed_count})")
        self.completed_toggle.setEnabled(bool(completed))
        self.completed_items.clear()
        for item in completed:
            output_count = len(item.outputs)
            suffix = f"  ·  {output_count} output{'s' if output_count != 1 else ''}" if output_count else ""
            row = QListWidgetItem(f"{Path(item.path).name}{suffix}")
            row.setToolTip(item.path)
            self.completed_items.addItem(row)
        self.completed_items.setVisible(self.completed_toggle.isChecked() and bool(completed))

    def _toggle_completed(self, expanded: bool) -> None:
        self.completed_toggle.setText("Hide files  ▴" if expanded else "Show files  ▾")
        self.completed_items.setVisible(expanded and self.completed_items.count() > 0)

    def mount_details(self, details: QWidget) -> None:
        """Reuse the existing output list and activity log in the review tabs."""
        self.details = details
        activity = details.widget(0)
        outputs = details.widget(1)
        details.removeTab(1)
        details.removeTab(0)
        self._outputs = outputs
        self.preview.addTab(outputs, "Files")
        self.preview.addTab(activity, "Activity")

    def update_status(self, item: QueueItem | None, running: bool = False, translating: bool | None = None) -> None:
        if item is None:
            self.file_label.setText("Queue finished" if not running else "Preparing next file…")
            self.file_label.setToolTip("")
            self.stage_label.setText("All queued work is complete." if not running else "Waiting for the next file.")
            self.elapsed_label.setText("Elapsed   —")
            self.remaining_label.setText("Remaining   —")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress_value.setText("0%")
            self._update_preview((), translating)
            return

        name = Path(item.path).name
        state = item.stage or item.status.title()
        elapsed = max(0, int(item.elapsed))
        elapsed_text = self._format_time(elapsed)
        self.file_label.setText(name)
        self.file_label.setToolTip(item.error or item.path)
        icon = QFileIconProvider().icon(QFileInfo(item.path))
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.media_icon.setPixmap(icon.pixmap(48, 48))
        self.stage_label.setText(state)
        self.elapsed_label.setText(f"Elapsed   {elapsed_text}")
        progress = item.progress
        if progress is None and running:
            self.progress.setRange(0, 0)
            self.progress_value.setText("…")
            self.remaining_label.setText("Remaining   estimating…")
        else:
            self.progress.setRange(0, 100)
            fraction = max(0.0, min(1.0, progress or 0.0))
            percent = round(fraction * 100)
            self.progress.setValue(percent)
            self.progress_value.setText(f"{percent}%")
            if running and fraction > 0:
                remaining = round(elapsed * (1 - fraction) / fraction)
                self.remaining_label.setText(f"Remaining   {self._format_time(remaining)}")
            elif item.status == "completed":
                self.remaining_label.setText("Remaining   00:00")
            else:
                self.remaining_label.setText("Remaining   —")
        self._update_preview(tuple(item.outputs), translating)

    def _select_tab(self, name: str) -> None:
        for index in range(self.preview.count()):
            if self.preview.tabText(index) == name:
                self.preview.setCurrentIndex(index)
                return

    def _sync_subtitle_view(self, index: int) -> None:
        # The mockup uses a cue table; retain the raw preview widget for API compatibility.
        if self.preview.widget(index) is self.subtitle_rows:
            self.subtitle_rows.show()

    @staticmethod
    def _format_time(seconds: int) -> str:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _update_preview(self, paths: tuple[str, ...], translating: bool | None) -> None:
        key = (paths, translating)
        if key == self._preview_key:
            return
        self._preview_key = key
        if not self._history_loaded:
            self.completed_label.setText(f"✓   Completed files ({len(paths)})")
        self.files_button.setEnabled(bool(paths) and self._outputs is not None)
        if not paths:
            for widget in (self.text_preview, self.translation_preview, self.subtitle_preview):
                widget.setPlainText("Select a completed queue item to review its generated files.")
            self.subtitle_rows.setRowCount(0)
            return
        text_paths = [Path(path) for path in paths if Path(path).suffix.lower() == ".txt"]
        subtitle_paths = [Path(path) for path in paths if Path(path).suffix.lower() in (".srt", ".vtt")]
        final_text = next((path for path in text_paths if ".source." not in path.name and ".intermediate." not in path.name), None)
        source_text = next((path for path in text_paths if ".source." in path.name), None)
        final_subtitles = next((path for path in subtitle_paths if ".source." not in path.name and ".intermediate." not in path.name), None)
        if translating is None:
            text = self._read_preview(source_text or final_text, "No TXT output was saved for this item.")
            self.text_preview.setPlainText("Translation status was not saved for this queue entry. This text may be translated.\n\n" + text)
            self.translation_preview.setPlainText("Translation status was not saved for this queue entry. Inspect Files to open the generated outputs.")
        else:
            self.text_preview.setPlainText(self._read_preview(source_text if translating else final_text, "No original transcript was saved for this file."))
            self.translation_preview.setPlainText(self._read_preview(final_text if translating else None, "No translated TXT output was saved for this file."))
        subtitles = self._read_preview(final_subtitles, "No subtitle file was generated for this item.")
        self.subtitle_preview.setPlainText(subtitles)
        self._show_subtitle_rows(subtitles if final_subtitles else "")

    def _show_subtitle_rows(self, content: str) -> None:
        cues: list[tuple[str, str]] = []
        for block in content.replace("\r\n", "\n").split("\n\n"):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timing = next((index for index, line in enumerate(lines) if "-->" in line), None)
            if timing is not None and timing + 1 < len(lines):
                cues.append((lines[timing].split("-->", 1)[0].strip(), " ".join(lines[timing + 1:])))
            if len(cues) >= 2000:
                break
        self.subtitle_rows.setRowCount(len(cues))
        for row, (timestamp, caption) in enumerate(cues):
            self.subtitle_rows.setItem(row, 0, QTableWidgetItem(timestamp))
            self.subtitle_rows.setItem(row, 1, QTableWidgetItem(caption))

    @staticmethod
    def _read_preview(path: Path | None, missing: str) -> str:
        if path is None:
            return missing
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                content = stream.read(262145)
            return content[:262144] + ("\n… preview truncated" if len(content) > 262144 else "")
        except OSError as error:
            return f"Could not preview {path.name}: {error}"

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, QTimer, Signal, QUrl, QEvent
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QAction, QKeySequence, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QToolButton, QTableView, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QStyleOptionProgressBar, QStyle, QProgressBar,
    QPlainTextEdit, QTabWidget, QListWidget, QListWidgetItem, QFileDialog,
    QMessageBox, QFrame, QStackedWidget,
)

from .state import QueueItem, StateStore, local_media_path, MEDIA_EXTENSIONS, MAX_QUEUE
from .settings import GUIDE_URL, SettingsPanel


def byte_text(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value:.0f} B"
        value /= 1024
    return "Unavailable"


def elapsed_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds >= 3600 else f"{seconds // 60:02d}:{seconds % 60:02d}"


class QueueModel(QAbstractTableModel):
    headings = ("Media", "Size", "Stage progress", "Status", "Elapsed")

    def __init__(self, items: list[QueueItem], parent=None):
        super().__init__(parent)
        self.items = items
        self._sizes = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headings)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            return (
                "Local audio or video file. Hover over a row to see its full path and any error.",
                "Source file size on disk, not the size of the generated outputs.",
                "Progress within the current processing stage, not the whole file or queue. Working means no percentage is available.",
                "Queue state, or the current stage while the file is running. Failed and cancelled files can be requeued with Retry.",
                "Time spent processing this file, excluding time waiting in the queue.",
            )[section]
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headings[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.path + ("\n" + item.error if item.error else "")
        if role == Qt.ItemDataRole.UserRole:
            return {"progress": item.progress, "status": item.status, "stage": item.stage}
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 3:
            if sys.platform == "darwin" and QApplication.palette().color(QPalette.ColorRole.Base).lightness() < 128:
                return QColor({"completed": "#65d6ad", "failed": "#ff8b82", "cancelled": "#e5bf76", "running": "#71d2d6"}.get(item.status, "#bec6cc"))
            return QColor({"completed": "#167759", "failed": "#b33d35", "cancelled": "#916318", "running": "#147e86"}.get(item.status, "#60676d"))
        if role == Qt.ItemDataRole.DisplayRole:
            if item.path not in self._sizes:
                try:
                    self._sizes[item.path] = Path(item.path).stat().st_size
                except OSError:
                    self._sizes[item.path] = None
            return (Path(item.path).name, byte_text(self._sizes[item.path]), "", item.stage if item.status == "running" else item.status.title(), elapsed_text(item.elapsed))[index.column()]
        return None

    def refresh(self):
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(self.headings) - 1))

    def replace(self, items):
        self.beginResetModel()
        self.items[:] = items
        self.endResetModel()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        value = index.data(Qt.ItemDataRole.UserRole)
        progress = value.get("progress")
        rect = option.rect.adjusted(6, 10, -6, -10)
        percent = max(0, min(100, round((progress or 0) * 100)))
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e2e9ec"))
        painter.drawRoundedRect(rect, 3, 3)
        fill = rect.adjusted(0, 0, 0, 0)
        fill.setWidth(round(rect.width() * percent / 100))
        painter.setBrush(QColor("#71bec0"))
        painter.drawRoundedRect(fill, 3, 3)
        painter.setPen(QColor("#173b43"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Working" if progress is None and value["status"] == "running" else f"{percent}%")
        painter.restore()


class JobThread(QThread):
    event = Signal(dict)

    def __init__(self, options, runner=None, parent=None):
        super().__init__(parent)
        self.options = options
        self.runner = runner
        self.cancel_event = threading.Event()
        self.result = None

    def run(self):
        try:
            from ..api import JobOptions, JobRunner
            if self.runner is None:
                self.runner = JobRunner()
            result = self.runner.run(JobOptions.from_mapping(self.options), progress=self.event.emit, cancel=self.cancel_event.is_set)
            self.result = {"status": result.status, "outputs": list(result.outputs), "error": result.error or "", "warnings": list(result.warnings)}
        except Exception as error:
            self.result = {"status": "cancelled" if self.cancel_event.is_set() else "failed", "outputs": [], "error": str(error), "warnings": []}


class ResourceThread(QThread):
    sample_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stop_event = threading.Event()
        self.disk_path = str(Path.home())

    def run(self):
        from ..resources import ResourceMonitor
        monitor = ResourceMonitor(disk_path=self.disk_path)
        try:
            while not self.stop_event.is_set():
                try:
                    monitor.disk_path = self.disk_path
                    self.sample_ready.emit(monitor.sample())
                except Exception as error:
                    self.sample_ready.emit({"monitor_error": str(error)})
                self.stop_event.wait(1.0)
        finally:
            monitor.close()


class CacheThread(QThread):
    result = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            from ..translation_pipeline import clear_translation_cache
            clear_translation_cache(self.path)
            self.result.emit("Transcript and translation caches cleared. Models retained.")
        except Exception as error:
            self.result.emit(f"Cache clear failed: {error}")


class ResourceStrip(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("resourceStrip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(20)
        self.meters = {}
        self.network_totals = None
        meter_help = {
            "cpu": "System CPU load and this app's CPU use. Other applications also contribute to the system total.",
            "ram": "System memory used and total capacity, plus this app's memory use. The bar shows system memory usage.",
            "gpu": "NVIDIA GPU load and device memory use, including other applications. Unavailable means telemetry could not be read, not zero usage.",
            "network": "System-wide download/upload rates and transferred totals since launch, including traffic from other applications.",
            "disk": "Free space and total capacity for the monitored output location, or home folder before an output is selected. The bar shows used space, not disk activity.",
        }
        for key, title in (("cpu", "CPU"), ("ram", "MEMORY"), ("gpu", "GPU"), ("network", "NETWORK / SYSTEM"), ("disk", "DISK")):
            box = QWidget()
            box.setToolTip(meter_help[key])
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            heading, value, detail = QLabel(title), QLabel("Waiting"), QLabel("")
            heading.setObjectName("meterHeading")
            value.setObjectName("meterValue")
            detail.setObjectName("meterDetail")
            value.setTextFormat(Qt.TextFormat.PlainText)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            detail.setWordWrap(True)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            bar.setRange(0, 100)
            column.addWidget(heading)
            column.addWidget(value)
            column.addWidget(detail)
            if key == "network":
                self.network_totals = QLabel("")
                self.network_totals.setObjectName("meterDetail")
                self.network_totals.setWordWrap(True)
                column.addWidget(self.network_totals)
            else:
                column.addSpacing(14)
            column.addWidget(bar)
            layout.addWidget(box, 1)
            self.meters[key] = (value, detail, bar, box)
        self.setMinimumHeight(100)

    def update_sample(self, sample):
        if "monitor_error" in sample:
            self.setToolTip(sample["monitor_error"])
            return
        cpu, app = sample.get("cpu_percent"), sample.get("app_cpu_percent")
        self._set("cpu", f"{cpu:.0f}% system" if cpu is not None else "Unavailable", f"App {app:.1f}%" if app is not None else "App unavailable", cpu)
        used, total = sample.get("ram_used"), sample.get("ram_total")
        self._set("ram", f"{byte_text(used)} / {byte_text(total)}", f"App {byte_text(sample.get('app_ram'))}", 100 * used / total if used is not None and total else 0)
        gpus = sample.get("gpus") or []
        if gpus:
            gpu = gpus[0]
            percent = gpu.get("utilization")
            self._set("gpu", f"{percent:.0f}% load" if percent is not None else "Load unavailable", f"{byte_text(gpu.get('used'))} / {byte_text(gpu.get('total'))}", percent)
            self.meters["gpu"][3].setToolTip("\n".join(str(g.get("name", "GPU")) for g in gpus) + "\nDevice-wide usage, including other applications.")
        else:
            self._set("gpu", "Unavailable", "No supported telemetry", None)
            self.meters["gpu"][3].setToolTip(sample.get("gpu_error") or "GPU monitoring unavailable")
        rx, tx = sample.get("network_rx_rate"), sample.get("network_tx_rate")
        self._set("network", f"Down {byte_text(rx)}/s", f"Up {byte_text(tx)}/s", 0)
        self.network_totals.setText(f"Total {byte_text(sample.get('network_rx_total'))} in / {byte_text(sample.get('network_tx_total'))} out")
        self.meters["network"][3].setToolTip(f"System-wide traffic since launch\nReceived {byte_text(sample.get('network_rx_total'))}\nSent {byte_text(sample.get('network_tx_total'))}")
        free, total = sample.get("disk_free"), sample.get("disk_total")
        self._set("disk", f"{byte_text(free)} free", f"{byte_text(total)} total", 100 * (total - free) / total if total and free is not None else 0)

    def _set(self, key, value, detail, percent):
        value_label, detail_label, bar, _ = self.meters[key]
        value_label.setText(value)
        detail_label.setText(detail)
        bar.setValue(round(percent or 0))


class MainWindow(QMainWindow):
    def __init__(self, store=None, monitor=True, runner=None):
        super().__init__()
        self.setWindowTitle("Auto Subtitle Plus")
        self.resize(1100, 780)
        self.setMinimumSize(960, 660)
        self.setAcceptDrops(True)
        self.store = store or StateStore()
        saved, self.items = self.store.load()
        self.runner = runner
        self.worker = None
        self.cache_worker = None
        self.running = False
        self.pause_after_current = False
        self.closing = False
        self.active_item = None
        self.started_at = 0.0
        self.batch_settings = {}
        self.batch_errors = 0
        self.resource_thread = None
        self._build_ui()
        try:
            self.settings.restore(saved)
        except (ValueError, TypeError):
            self.log("Saved settings were invalid; defaults retained.")
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save_state)
        self.settings.changed.connect(lambda: self.save_timer.start())
        self.settings.clear_cache_requested.connect(self.clear_cache)
        self.tick = QTimer(self)
        self.tick.setInterval(500)
        self.tick.timeout.connect(self._tick)
        self.tick.start()
        if monitor:
            self.resource_thread = ResourceThread(self)
            self.resource_thread.sample_ready.connect(self.resources.update_sample)
            self.resource_thread.start()
        self.update_controls()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(16, 12, 16, 10)
        page.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Auto Subtitle Plus")
        title.setObjectName("appTitle")
        header.addWidget(title)
        if sys.platform == "darwin":
            local = QLabel("On your Mac")
            local.setObjectName("muted")
            local.setToolTip("Media and subtitle processing stay on this Mac. Missing models may download unless Offline is enabled.")
            header.addWidget(local)
        header.addStretch()
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        header.addWidget(self.summary)
        guide = QLabel(f'<a href="{GUIDE_URL}#queue">Queue guide</a>')
        guide.setOpenExternalLinks(True)
        guide.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        guide.setToolTip(f"Open help for the queue, progress, outputs and resource meters in your browser.\n{GUIDE_URL}#queue")
        header.addWidget(guide)
        about = self.tool_button(QStyle.StandardPixmap.SP_MessageBoxInformation, "Credits and version", self.show_credits)
        header.addWidget(about)
        page.addLayout(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        page.addWidget(splitter, 1)
        queue_panel = QWidget()
        queue_layout = QVBoxLayout(queue_panel)
        queue_layout.setContentsMargins(0, 0, 8, 0)
        toolbar = QHBoxLayout()
        self.add_button = QPushButton("Add files")
        self.add_button.setToolTip("Add local audio or video files to the queue. You can also drag files onto this window. Duplicate paths are skipped.")
        self.add_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.add_button.clicked.connect(self.choose_files)
        toolbar.addWidget(self.add_button)
        self.folder_button = self.tool_button(QStyle.StandardPixmap.SP_DirIcon, "Add supported media from a local folder. Subfolders are not scanned.", self.choose_folder)
        toolbar.addWidget(self.folder_button)
        toolbar.addStretch()
        self.up_button = self.tool_button(QStyle.StandardPixmap.SP_ArrowUp, "Move selected file up", lambda: self.move_selected(-1))
        self.down_button = self.tool_button(QStyle.StandardPixmap.SP_ArrowDown, "Move selected file down", lambda: self.move_selected(1))
        self.retry_button = self.tool_button(QStyle.StandardPixmap.SP_BrowserReload, "Requeue selected files, then use Start queue to process them with the current settings. This does not enable cached-only Retry translation.", self.retry_selected)
        self.remove_button = self.tool_button(QStyle.StandardPixmap.SP_DialogDiscardButton, "Remove selected files from the queue. Source files and generated outputs stay on disk.", self.remove_selected)
        self.clear_button = self.tool_button(QStyle.StandardPixmap.SP_TrashIcon, "Remove completed entries from the queue. Keep their source files and outputs on disk.", self.clear_completed)
        for button in (self.up_button, self.down_button, self.retry_button, self.remove_button, self.clear_button):
            toolbar.addWidget(button)
        queue_layout.addLayout(toolbar)
        self.model = QueueModel(self.items, self)
        self.table = QTableView()
        modifier = "Command" if sys.platform == "darwin" else "Ctrl"
        self.table.setToolTip(f"Select a file to see its outputs. {modifier}-click or Shift-click to select several files. Reordering and removing entries are available while idle.")
        self.table.setObjectName("fileQueue")
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(2, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 76), (2, 116), (3, 99), (4, 64)):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().selectionChanged.connect(self.show_selected_outputs)
        self.queue_stack = QStackedWidget()
        self.queue_stack.addWidget(self.table)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel("Your next subtitles start here.")
        empty_title.setObjectName("emptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        hint = QLabel("Drop audio or video here to create subtitles.\nOriginal-language subtitles are the default.")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        empty_layout.addWidget(hint)
        empty_add = QPushButton("Choose files…")
        empty_add.clicked.connect(self.choose_files)
        empty_layout.addWidget(empty_add, alignment=Qt.AlignmentFlag.AlignCenter)
        self.queue_stack.addWidget(empty)
        queue_layout.addWidget(self.queue_stack, 1)
        self.current_label = QLabel("Ready")
        self.current_label.setToolTip("Latest processing stage or result for the active file. Open Activity below for details and errors.")
        self.current_label.setObjectName("currentStage")
        self.current_label.setTextFormat(Qt.TextFormat.PlainText)
        self.current_label.setWordWrap(True)
        self.current_label.setMinimumHeight(34)
        queue_layout.addWidget(self.current_label)
        self.current_progress = QProgressBar()
        self.current_progress.setToolTip("Progress of the current stage. It can reset when a new stage begins; a moving indicator means the stage has no measurable percentage.")
        self.current_progress.setRange(0, 100)
        self.current_progress.setValue(0)
        self.current_progress.setFixedHeight(18)
        queue_layout.addWidget(self.current_progress)
        self.details = QTabWidget()
        self.details.setMaximumHeight(155)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.document().setMaximumBlockCount(1200)
        self.activity.setObjectName("activityLog")
        if sys.platform == "darwin":
            self.activity.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.outputs = QListWidget()
        self.outputs.itemDoubleClicked.connect(self.open_output)
        self.details.addTab(self.activity, "Activity")
        self.details.addTab(self.outputs, "Outputs")
        self.details.setTabToolTip(0, "Processing messages, warnings and errors for this session. The most recent 1,200 lines are retained.")
        self.details.setTabToolTip(1, "Generated files and errors for selected queue entries. Double-click an output to open it.")
        self.activity.setToolTip(f"Recent processing messages. Select text and press {modifier}+C to copy it. Logs may contain local paths or content; review before sharing.")
        self.outputs.setToolTip("Outputs for selected queue files. Hover for a full path; double-click a file to open it in its default application.")
        queue_layout.addWidget(self.details)
        controls = QHBoxLayout()
        self.start_button = QPushButton("Start queue")
        self.start_button.setToolTip("Process queued files one at a time using the current settings. Requires at least one queued file; settings are locked during processing and cache clearing.")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self.start_queue)
        self.pause_button = QPushButton("Pause queue")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_button.setToolTip("Finish the active file, then pause before the next file.")
        self.pause_button.clicked.connect(self.pause_queue)
        self.cancel_button = self.tool_button(QStyle.StandardPixmap.SP_MediaStop, "Cancel active file and pause queue", self.cancel_current)
        self.open_button = self.tool_button(QStyle.StandardPixmap.SP_DirOpenIcon, "Open selected output folder", self.open_output_folder)
        controls.addWidget(self.start_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        controls.addWidget(self.open_button)
        queue_layout.addLayout(controls)
        splitter.addWidget(queue_panel)
        self.settings = SettingsPanel()
        self.settings.setMinimumWidth(345)
        splitter.addWidget(self.settings)
        splitter.setSizes([655, 405])
        self.resources = ResourceStrip()
        self.summary.setToolTip("Total queue entries, completed files and files waiting to run. Failed or cancelled files must be requeued before Start will process them.")
        page.addWidget(self.resources)
        self._build_menus()
        if sys.platform == "darwin":
            for label in self.findChildren(QLabel):
                name = label.objectName()
                size = {"appTitle": 21, "emptyTitle": 24, "muted": 12, "meterDetail": 12, "meterHeading": 10}.get(name)
                if size is not None or name in ("meterValue", "currentStage"):
                    font = label.font()
                    if size is not None:
                        font.setPixelSize(size)
                    font.setBold(name in ("appTitle", "emptyTitle", "meterHeading", "meterValue", "currentStage"))
                    label.setFont(font)
            font = self.start_button.font()
            font.setBold(True)
            self.start_button.setFont(font)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        self.open_action = file_menu.addAction("Add Files…")
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_files)
        file_menu.addAction("Add Folder…", self.choose_folder)
        file_menu.addSeparator()
        file_menu.addAction("Open Output Folder", self.open_output_folder)
        close = file_menu.addAction("Close Window", self.close)
        close.setShortcut(QKeySequence.StandardKey.Close)
        quit_action = file_menu.addAction("Quit Auto Subtitle Plus", self.close)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        queue_menu = self.menuBar().addMenu("Queue")
        self.start_action = queue_menu.addAction("Start Queue", self.start_button.click)
        self.start_action.setShortcut("Ctrl+Return")
        self.pause_action = queue_menu.addAction("Pause After Current File", self.pause_button.click)
        self.cancel_action = queue_menu.addAction("Cancel Current File", self.cancel_button.click)
        help_menu = self.menuBar().addMenu("Help")
        about = help_menu.addAction("About Auto Subtitle Plus", self.show_credits)
        about.setMenuRole(QAction.MenuRole.AboutRole)

    def tool_button(self, icon, tooltip, callback):
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(callback)
        return button

    def choose_files(self):
        patterns = " ".join("*" + ext for ext in sorted(MEDIA_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add local media", "", f"Audio and video ({patterns})")
        self.add_paths(paths)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add local folder")
        if folder:
            if folder.startswith(("\\\\", "//")):
                self.log("Only local folders are accepted.")
                return
            try:
                self.add_paths([str(path) for path in sorted(Path(folder).iterdir()) if path.suffix.lower() in MEDIA_EXTENSIONS and path.is_file()])
            except OSError as error:
                self.log(f"Cannot read folder: {error}")

    def add_paths(self, paths):
        known = {os.path.normcase(item.path) for item in self.items}
        added, errors = [], []
        for value in paths:
            if len(self.items) + len(added) >= MAX_QUEUE:
                errors.append(f"Queue limit: {MAX_QUEUE} files.")
                break
            try:
                path = local_media_path(value)
                if os.path.normcase(path) not in known:
                    added.append(QueueItem(path))
                    known.add(os.path.normcase(path))
            except ValueError as error:
                errors.append(str(error))
        if added:
            self.model.replace([*self.items, *added])
            self.log(f"Added {len(added)} file(s).")
        for error in errors:
            self.log(error)
        self.save_state()
        self.update_controls()
        return len(added)

    def selected_rows(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows())

    def move_selected(self, direction):
        rows = self.selected_rows()
        if self.running or len(rows) != 1:
            return
        current, target = rows[0], rows[0] + direction
        if 0 <= target < len(self.items):
            items = list(self.items)
            items[current], items[target] = items[target], items[current]
            self.model.replace(items)
            self.table.selectRow(target)
            self.save_state()

    def remove_selected(self):
        if self.running:
            return
        rows = set(self.selected_rows())
        self.model.replace([item for index, item in enumerate(self.items) if index not in rows])
        self.save_state()
        self.update_controls()

    def clear_completed(self):
        if not self.running:
            self.model.replace([item for item in self.items if item.status != "completed"])
            self.save_state()
            self.update_controls()

    def retry_selected(self):
        if self.running:
            return
        for row in self.selected_rows():
            item = self.items[row]
            item.status, item.stage, item.progress, item.error = "queued", "Queued", 0.0, ""
        self.model.refresh()
        self.save_state()
        self.update_controls()

    def start_queue(self):
        if self.running or self.cache_worker:
            return
        try:
            from ..api import JobOptions
            settings = self.settings.options()
            self.settings.validate_platform(settings)
            queued = [item for item in self.items if item.status == "queued"]
            if not queued:
                return
            destinations = set()
            for item in queued:
                local_media_path(item.path)
                JobOptions.from_mapping({**settings, "path": item.path})
                destination = os.path.normcase(str(Path(settings.get("output_dir") or Path(item.path).parent) / Path(item.path).stem))
                if destination in destinations:
                    raise ValueError("Queued files would use the same output filename. Save beside each input or process them separately.")
                destinations.add(destination)
            if settings.get("overwrite") and QMessageBox.question(self, "Replace outputs?", "Existing output files may be replaced for every queued item. Continue?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.batch_settings = settings
        except (ValueError, TypeError, ImportError) as error:
            QMessageBox.warning(self, "Check queue settings", str(error))
            return
        self.running, self.pause_after_current = True, False
        self.batch_errors = 0
        self.update_controls()
        self._next_job()

    def _next_job(self):
        if self.pause_after_current or self.closing:
            self._finish_queue()
            return
        item = next((item for item in self.items if item.status == "queued"), None)
        if item is None:
            self._finish_queue()
            return
        self.active_item = item
        item.status, item.stage, item.progress, item.error = "running", "Starting", None, ""
        self.started_at = time.monotonic()
        self.table.selectRow(self.items.index(item))
        self.worker = JobThread({**self.batch_settings, "path": item.path}, self.runner, self)
        self.worker.event.connect(self._job_event)
        self.worker.finished.connect(self._job_finished)
        self.log(f"Started {Path(item.path).name}")
        self.save_state()
        self.worker.start()

    def _job_event(self, event):
        if self.active_item is None:
            return
        state = str(event.get("state", "working"))
        message = str(event.get("message") or event.get("file") or state)
        if state != "log":
            self.active_item.stage = state.replace("_", " ").title()
            progress = event.get("progress")
            if progress is None and event.get("total_bytes", 0):
                progress = event.get("completed_bytes", 0) / event["total_bytes"]
            self.active_item.progress = float(progress) if isinstance(progress, (int, float)) else None
            self.current_label.setText(message[:300])
        if state in ("log", "notice", "warning", "failed", "completed") or event.get("cached"):
            self.log(message)
        self.model.refresh()
        self._update_progress()

    def _job_finished(self):
        thread = self.worker
        self.runner = thread.runner
        result = thread.result or {"status": "failed", "error": "Worker stopped without a result.", "outputs": [], "warnings": []}
        item = self.active_item
        item.status = result["status"]
        if item.status == "failed":
            self.batch_errors += 1
        item.stage = item.status.title()
        item.progress = 1.0 if item.status == "completed" else 0.0
        item.error = result["error"]
        item.outputs = result["outputs"]
        item.elapsed = time.monotonic() - self.started_at
        self.log(f"{item.stage}: {Path(item.path).name}" + (f" - {item.error}" if item.error else ""))
        for warning in result["warnings"]:
            self.log(f"Warning: {warning}")
        self.worker = None
        self.active_item = None
        thread.deleteLater()
        self.model.refresh()
        self.show_selected_outputs()
        self.save_state()
        QTimer.singleShot(0, self._next_job)

    def _finish_queue(self):
        self.running = False
        if self.pause_after_current:
            self.current_label.setText("Queue paused")
        elif self.batch_errors:
            self.current_label.setText(f"Queue finished — {self.batch_errors} failed")
        else:
            self.current_label.setText("Queue finished")
        self.current_progress.setRange(0, 100)
        self.current_progress.setValue(0 if self.pause_after_current or self.batch_errors else 100)
        self.update_controls()
        self.save_state()
        if self.closing:
            self.close()

    def pause_queue(self):
        self.pause_after_current = True
        self.pause_button.setText("Pausing after file")
        self.pause_button.setEnabled(False)
        self.pause_action.setEnabled(False)

    def cancel_current(self):
        if self.worker:
            self.pause_after_current = True
            self.worker.cancel_event.set()
            self.current_label.setText("Cancelling active file...")
            self.cancel_button.setEnabled(False)
            self.cancel_action.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.pause_action.setEnabled(False)

    def _tick(self):
        if self.resource_thread:
            path = self.batch_settings.get("output_dir") if self.running else self.settings.snapshot().get("output_dir")
            if self.active_item and not path:
                path = str(Path(self.active_item.path).parent)
            self.resource_thread.disk_path = path or str(Path.home())
        if self.active_item:
            self.active_item.elapsed = time.monotonic() - self.started_at
            self.model.refresh()
        self._update_progress()

    def _update_progress(self):
        if self.active_item:
            value = self.active_item.progress
            self.current_progress.setRange(0, 0 if value is None else 100)
            if value is not None:
                self.current_progress.setValue(round(max(0, min(1, value)) * 100))
        done = sum(item.status == "completed" for item in self.items)
        queued = sum(item.status == "queued" for item in self.items)
        noun = "file" if len(self.items) == 1 else "files"
        self.summary.setText(f"{len(self.items)} {noun}  |  {done} completed  |  {queued} queued")

    def update_controls(self):
        self.start_button.setEnabled(not self.running and self.cache_worker is None and any(item.status == "queued" for item in self.items))
        self.pause_button.setText("Pausing after file" if self.running and self.pause_after_current else "Pause queue")
        self.pause_button.setEnabled(self.running and not self.pause_after_current)
        cancelling = self.worker is not None and self.worker.cancel_event.is_set()
        self.cancel_button.setEnabled(self.running and not cancelling)
        self.start_action.setEnabled(self.start_button.isEnabled())
        self.pause_action.setEnabled(self.pause_button.isEnabled())
        self.cancel_action.setEnabled(self.cancel_button.isEnabled())
        self.queue_stack.setCurrentIndex(0 if self.items else 1)
        self.settings.setEnabled(not self.running and self.cache_worker is None)
        for button in (self.up_button, self.down_button, self.remove_button, self.clear_button, self.retry_button):
            button.setEnabled(not self.running)
        self._update_progress()

    def show_selected_outputs(self, *_):
        self.outputs.clear()
        rows = self.selected_rows()
        for row in rows:
            item = self.items[row]
            for path in item.outputs:
                entry = QListWidgetItem(Path(path).name)
                entry.setData(Qt.ItemDataRole.UserRole, path)
                entry.setToolTip(path + "\nDouble-click to open in the default application.")
                self.outputs.addItem(entry)
            if item.error:
                self.outputs.addItem(item.error)

    def open_output_folder(self):
        rows = self.selected_rows()
        if not rows:
            return
        item = self.items[rows[0]]
        folder = Path(item.outputs[0]).parent if item.outputs else Path(item.path).parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def open_output(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def clear_cache(self):
        if self.running or self.cache_worker:
            return
        answer = QMessageBox.question(self, "Clear stage cache?", "Remove cached transcripts and translations? Downloaded models will be retained.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cache_worker = CacheThread(self.settings.snapshot().get("translation_cache_dir") or None, self)
        self.cache_worker.result.connect(self.log)
        self.cache_worker.finished.connect(self._cache_finished)
        self.cache_worker.start()
        self.update_controls()

    def _cache_finished(self):
        self.cache_worker.deleteLater()
        self.cache_worker = None
        self.update_controls()
        if self.closing:
            self.close()

    def save_state(self):
        try:
            self.store.save(self.settings.snapshot(), self.items)
        except OSError as error:
            self.log(f"Could not save desktop state: {error}")

    def log(self, message):
        self.activity.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(url.isLocalFile() for url in urls):
            self.add_paths([url.toLocalFile() for url in urls])
            event.acceptProposedAction()

    def show_credits(self):
        QMessageBox.about(self, "Auto Subtitle Plus / Credits", "Auto Subtitle Plus\n\nShared local processing library, CLI and Qt desktop.\n\nQt / PySide6: The Qt Company, LGPLv3 community libraries.\npsutil: Giampaolo Rodola and contributors, BSD-3-Clause.\nSubtitle Edit: Nikolaj Olsson and contributors, MIT; compact desktop workflow inspiration, no code copied.\n\nWhisper, Stable-ts, faster-whisper, llama.cpp, CTranslate2, Hugging Face, Tencent, Meta, Helsinki-NLP and Google model authors.\n\nComplete repository links, fork credits and model licenses are in README.md.")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            if not self.closing and QMessageBox.question(self, "Cancel and close?", "Cancel the active file and close the application? Pending files will remain in the queue.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.closing = True
            self.cancel_current()
            event.ignore()
            return
        if self.cache_worker and self.cache_worker.isRunning():
            self.closing = True
            event.ignore()
            return
        if self.resource_thread:
            self.resource_thread.stop_event.set()
            self.resource_thread.wait(3000)
        if self.runner:
            self.runner.close()
        self.save_state()
        event.accept()


STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 12px; color: #263239; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: #f4f6f7; }
QLabel#appTitle { font-size: 20px; font-weight: 650; color: #18272e; }
QLabel#muted, QLabel#meterDetail { color: #63717a; }
QLabel#currentStage { font-weight: 600; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #cbd4d9; border-radius: 4px; padding: 6px 10px; min-height: 18px; }
QToolButton { padding: 6px; }
QPushButton:hover, QToolButton:hover { background: #e8f1f2; border-color: #91afb7; }
QPushButton:disabled, QToolButton:disabled { color: #9aa5aa; background: #f1f3f4; border-color: #dfe4e7; }
QPushButton#primary { background: #167b7e; color: white; border-color: #167b7e; font-weight: 600; }
QPushButton#primary:hover { background: #116a6d; }
QPushButton#primary:disabled { background: #cad6d8; border-color: #cad6d8; color: #738388; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget { background: white; border: 1px solid #cbd4d9; border-radius: 3px; padding: 4px; min-height: 20px; }
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled { background: #eef1f3; color: #88959c; }
QCheckBox { spacing: 7px; min-height: 22px; }
QTableView { background: white; alternate-background-color: #f8fafb; border: 1px solid #d6dfe3; selection-background-color: #dceff0; selection-color: #173b43; }
QHeaderView::section { background: #eef2f4; color: #5b6a73; border: none; border-bottom: 1px solid #d6dfe3; padding: 8px 5px; text-align: left; }
QTabWidget::pane { border: 1px solid #d6dfe3; background: #f4f6f7; }
QTabBar::tab { background: #e9eef0; border: none; padding: 8px 9px; color: #5a6a73; }
QTabBar::tab:selected { background: white; color: #145f65; border-bottom: 2px solid #1c8586; }
QProgressBar { background: #e2e9ec; border: none; border-radius: 3px; color: #173b43; text-align: center; font-size: 10px; }
QProgressBar::chunk { background: #71bec0; border-radius: 3px; }
QFrame#resourceStrip { background: white; border-top: 1px solid #d5dfe4; }
QLabel#meterHeading { color: #637580; font-size: 10px; font-weight: 600; }
QLabel#meterValue { color: #213c47; font-weight: 600; }
QLabel#meterDetail { font-size: 10px; }
QPlainTextEdit#activityLog { font-family: Consolas; font-size: 11px; border-radius: 0; }
QSplitter::handle { background: #e3e9ec; }
"""


def apply_theme(app):
    if sys.platform == "darwin":
        app.setStyleSheet("")
        return
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#f4f6f7"), (QPalette.ColorRole.WindowText, "#263239"),
        (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.AlternateBase, "#f8fafb"),
        (QPalette.ColorRole.Text, "#263239"), (QPalette.ColorRole.Button, "#ffffff"),
        (QPalette.ColorRole.ButtonText, "#263239"), (QPalette.ColorRole.Highlight, "#167b7e"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.Light, "#ffffff"),
        (QPalette.ColorRole.Midlight, "#edf2f4"), (QPalette.ColorRole.Mid, "#bbc8cf"),
        (QPalette.ColorRole.Dark, "#63717a"), (QPalette.ColorRole.Shadow, "#263239"),
        (QPalette.ColorRole.ToolTipBase, "#ffffff"), (QPalette.ColorRole.ToolTipText, "#263239"),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#88959c"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#88959c"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


class DesktopApplication(QApplication):
    """Handle files opened through Finder before or after window creation."""

    def __init__(self, argv):
        self.window = None
        self.pending_files = []
        super().__init__(argv)

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()
            if path and Path(path).resolve() == Path(sys.argv[0]).resolve():
                return True
            if path:
                if self.window is None:
                    self.pending_files.append(path)
                else:
                    self.window.add_paths([path])
                    self.window.showNormal()
                    self.window.raise_()
                    self.window.activateWindow()
            return True
        return super().event(event)


def launch():
    app = QApplication.instance() or DesktopApplication(sys.argv)
    app.setApplicationName("Auto Subtitle Plus")
    app.setOrganizationName("AutoSubtitlePlus")
    apply_theme(app)
    window = MainWindow()
    if isinstance(app, DesktopApplication):
        app.window = window
        if app.pending_files:
            window.add_paths(app.pending_files)
            app.pending_files.clear()
    window.show()
    if len(sys.argv) > 1:
        window.add_paths(sys.argv[1:])
    return app.exec()

"""Offline guidance and the online user guide for the macOS desktop."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..guidance import HARDWARE_GUIDE, TAB_GUIDANCE
from ..settings import GUIDE_URL


class HelpScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        title = QLabel("Help")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        intro = QLabel("Quick guidance is available offline. Open the user guide for full workflow and option details.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(True)
        self.content.setReadOnly(True)
        self.content.setHtml(self._guidance_html())
        layout.addWidget(self.content, 1)
        self.guide_button = QPushButton("Open online user guide")
        self.guide_button.setToolTip(f"Open the complete guide in your browser.\n{GUIDE_URL}")
        self.guide_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GUIDE_URL)))
        layout.addWidget(self.guide_button, alignment=Qt.AlignmentFlag.AlignRight)

    @staticmethod
    def _guidance_html() -> str:
        sections = [
            ("Queue and processing", "Add local audio or video files to the queue, choose Speech and Translate options, then start processing. The queue runs one file at a time. Use Activity for messages and Outputs for generated files."),
            *TAB_GUIDANCE.items(),
            ("Hardware", HARDWARE_GUIDE),
        ]
        return "<h2>Using the app</h2>" + "".join(
            f"<h3>{title}</h3><p>{text}</p>" for title, text in sections
        )

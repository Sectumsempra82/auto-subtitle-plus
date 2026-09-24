"""Mac settings forms built from the existing, behavior-owning controls."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import heading_icon

FIELD_HINTS = {
    "Backend": "Speech recognition engine to use.",
    "Model": "Select the speech recognition model.",
    "Audio language": "Language spoken in the audio.",
    "Device": "Auto selects an available processing device.",
    "Compute type": "Not supported on this Mac.",
    "Inference batch": "Larger batches need more memory.",
    "VAD": "Detect voice activity and skip silence.",
    "Word timestamps": "Include word-level timestamps.",
    "Enhance consistency": "Improve consistency across transcript segments.",
    "Translate": "Enable translation after transcription.",
    "Target language": "Language for translated subtitles.",
    "Provider": "Translation provider to use.",
    "Route": "How translation requests are routed.",
    "Model": "Translation model to use.",
    "Translation device": "CPU or Auto for local translation.",
    "Bilingual": "Include both original and translated text.",
    "Context": "Add topic or terminology to guide translation.",
    "Glossary JSON": "Preferred translations for specific terms.",
    "Layout": "Adaptive or preserve source cue timing.",
    "Max chars per line": "Target line length for translated captions.",
    "Max lines": "Maximum target lines per caption.",
    "Max CPS": "Target reading speed in characters per second.",
    "Min duration": "Shortest target caption duration in seconds.",
    "Max duration": "Longest target caption duration in seconds.",
    "Beside input": "Save output beside each source file.",
    "Use folder": "Save output to a shared folder.",
    "Output folder": "Destination used when folder output is selected.",
    "Subtitle file": "Write a separate subtitle file.",
    "Format": "SRT or WebVTT subtitle format.",
    "Transcript (TXT)": "Save editable transcript text.",
    "Extracted audio": "Keep extracted audio as an output.",
    "Video": "Create a subtitled video.",
    "Video mode": "Burn captions in or add a selectable track.",
    "Save original": "Save source-language subtitles separately.",
    "Save intermediate": "Keep intermediate English subtitles for via-en.",
    "Overwrite": "Allow existing output files to be replaced.",
    "Offline": "Prevent downloads and online translation.",
    "Retry translation": "Reuse a cached transcript for translation.",
    "Verbose": "Include more detail in processing activity.",
    "Extract workers": "Concurrent audio extraction workers.",
    "Cache folder": "Optional location for translation and stage caches.",
}


class MacForms(QWidget):
    """Presentation for Speech, Translate, and the remaining settings.

    SettingsPanel remains the owner of every control's state, validation,
    persistence, and signal wiring. Adding its widgets to these layouts
    reparents the same controls; no second settings model is introduced.
    """

    def __init__(self, settings: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.panel = settings
        self.pages = QStackedWidget(self)
        self._contents: dict[str, QWidget] = {}
        self.speech = self._page(
            "Speech", "Transcribe speech to text with local AI models. Everything runs on your Mac.",
        )
        self.translate = self._page(
            "Translate", "Translate transcripts to create subtitles in your target language.",
        )
        self.settings = self._settings_page()

        self.pages.addWidget(self.speech)
        self.pages.addWidget(self.translate)
        self.pages.addWidget(self.settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)
        self._build_speech()
        self._build_translate()
        self._build_settings()

    def show_speech(self) -> None:
        self.pages.setCurrentWidget(self.speech)

    def show_translate(self) -> None:
        self.pages.setCurrentWidget(self.translate)

    def show_settings(self, section: str | None = None) -> None:
        self.pages.setCurrentWidget(self.settings)
        if section in self._settings_buttons:
            self._settings_buttons[section].click()

    def _page(self, title: str, description: str) -> QWidget:
        page = QWidget()
        page.setObjectName("macFormPage")
        column = QVBoxLayout(page)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(12)
        heading = QHBoxLayout()
        glyph = QLabel()
        glyph.setObjectName("macFormGlyph")
        glyph.setPixmap(heading_icon(title).pixmap(QSize(28, 28)))
        glyph.setAccessibleName(f"{title} icon")
        heading.setSpacing(10)
        heading.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("macPageTitle")
        description_label = QLabel(description)
        description_label.setObjectName("macPageSubtitle")
        description_label.setWordWrap(True)
        words.addWidget(title_label)
        words.addWidget(description_label)
        heading.addLayout(words, 1)
        column.addLayout(heading)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        column.addWidget(scroll, 1)
        self._contents[title] = content
        return page

    def _card(self, parent: QWidget, title: str | None = None) -> tuple[QFrame, QGridLayout]:
        card = QFrame(parent)
        card.setObjectName("macCard")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 12, 14, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.setColumnMinimumWidth(0, 118)
        if title:
            label = QLabel(title)
            label.setObjectName("macCardTitle")
            grid.addWidget(label, 0, 0, 1, 3)
        return card, grid

    def _row(self, grid: QGridLayout, row: int, label: str, control: QWidget, help_text: str | None = None) -> None:
        name = QLabel(label)
        name.setObjectName("macFieldLabel")
        grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(control, row, 1)
        tip = help_text or FIELD_HINTS.get(label) or control.accessibleDescription() or control.toolTip()
        if isinstance(control, QCheckBox):
            control.setText(tip)
            grid.setColumnStretch(1, 3)
            grid.setColumnStretch(2, 4)
            return
        detail = QLabel(tip)
        detail.setObjectName("macFieldHint")
        detail.setWordWrap(True)
        detail.setMinimumWidth(100)
        detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        grid.addWidget(detail, row, 2)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 4)

    def _move(self, widget: QWidget, parent: QWidget) -> QWidget:
        widget.setParent(parent)
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())
        return widget

    def _build_speech(self) -> None:
        s = self.panel
        card, grid = self._card(self.speech, "Speech recognition")
        content = self._contents["Speech"]
        content.layout().addWidget(card)
        fields = (
            ("Backend", s.backend), ("Model", s.model), ("Audio language", s.language),
            ("Device", s.device), ("Compute type", s.compute_type),
            ("Inference batch", s.inference_batch_size),
        )
        for row, (label, widget) in enumerate(fields, 1):
            hint = "Higher models may be more accurate but slower." if label == "Model" else FIELD_HINTS[label]
            self._row(grid, row, label, self._move(widget, card), hint)
        checkboxes = (("VAD", s.vad), ("Word timestamps", s.word_timestamps), ("Enhance consistency", s.enhance_consistency))
        for offset, (label, widget) in enumerate(checkboxes, len(fields) + 1):
            self._row(grid, offset, label, self._move(widget, card), FIELD_HINTS[label])
        validation = self._move(s.asr_validation, card)
        advice = self._move(s.speech_advice, card)
        guide_button = self._move(s.hardware_help_button, card)
        guide = self._move(s.hardware_help, card)
        side = QFrame(card)
        side.setObjectName("macSideCard")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 8, 10, 8)
        side_title = QLabel("About speech recognition")
        side_title.setObjectName("macSideTitle")
        side_layout.addWidget(side_title)
        side_copy = QLabel("Transcribe audio to create subtitles or text transcripts. Backend is the engine; model is the trained speech recognizer.")
        side_copy.setWordWrap(True)
        side_layout.addWidget(side_copy)
        advice.hide()
        guide.hide()
        guide_button.setText("Hardware details…")
        guide_button.toggled.connect(advice.setVisible)
        side_layout.addWidget(guide_button)
        side_layout.addWidget(advice)
        side_layout.addWidget(guide)
        grid.addWidget(side, 7, 2, 3, 1)
        grid.addWidget(validation, 10, 0, 1, 3)
        content.layout().addStretch(1)

    def _build_translate(self) -> None:
        s = self.panel
        card, grid = self._card(self.translate, "Translation")
        content = self._contents["Translate"]
        content.layout().addWidget(card)
        fields = (
            ("Translate", s.translate_enabled), ("Target language", s.translate_to),
            ("Provider", s.translation_engine), ("Route", s.translation_route),
            ("Model", s.translation_model), ("Translation device", s.translation_device),
            ("Bilingual", s.bilingual), ("Context", s.context), ("Glossary JSON", s.glossary),
        )
        for row, (label, widget) in enumerate(fields, 1):
            moved = self._move(widget, card)
            hint = "Translation model to use." if label == "Model" else FIELD_HINTS[label]
            self._row(grid, row, label, moved, hint)
        route = self._move(s.route_preview, card)
        info = self._move(s.model_info, card)
        open_model = self._move(s.open_model, card)
        grid.addWidget(route, 10, 0, 1, 3)
        grid.addWidget(info, 11, 0, 1, 3)
        grid.addWidget(open_model, 12, 1, 1, 2, Qt.AlignmentFlag.AlignLeft)
        content.layout().addStretch(1)

    def _settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("macFormPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(12)
        title = QLabel("Settings")
        title.setObjectName("macPageTitle")
        desc = QLabel("Subtitle layout, output files, and system behavior.")
        desc.setObjectName("macPageSubtitle")
        outer.addWidget(title)
        outer.addWidget(desc)
        body = QHBoxLayout()
        body.setSpacing(12)
        self._settings_buttons: dict[str, QPushButton] = {}
        rail = QVBoxLayout()
        rail.setSpacing(4)
        self.settings_stack = QStackedWidget()
        for index, name in enumerate(("Layout", "Files", "System")):
            button = QPushButton(name)
            button.setObjectName("macSettingsNav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.settings_stack.setCurrentIndex(i))
            button.clicked.connect(lambda checked=False, selected=name: self._check_settings_button(selected))
            rail.addWidget(button)
            self._settings_buttons[name] = button
            self.settings_stack.addWidget(self._settings_section(name))
        rail.addStretch(1)
        body.addLayout(rail)
        body.addWidget(self.settings_stack, 1)
        outer.addLayout(body, 1)
        self._settings_buttons["Layout"].setChecked(True)
        return page

    def _check_settings_button(self, active: str) -> None:
        for name, button in self._settings_buttons.items():
            button.setChecked(name == active)

    def _settings_section(self, title: str) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        card, grid = self._card(content, title)
        column.addWidget(card)
        column.addStretch(1)
        area.setWidget(content)
        return area

    def _build_settings(self) -> None:
        s = self.panel
        cards = [self.settings_stack.widget(i).widget().layout().itemAt(0).widget() for i in range(3)]
        layout_card, files_card, system_card = cards
        layout_grid = layout_card.layout()
        files_grid = files_card.layout()
        system_grid = system_card.layout()
        layout_fields = (
            ("Layout", s.subtitle_layout), ("Max chars per line", s.max_chars_per_line),
            ("Max lines", s.max_lines), ("Max CPS", s.max_cps),
            ("Min duration", s.min_duration), ("Max duration", s.max_duration),
        )
        for row, (label, widget) in enumerate(layout_fields, 1):
            self._row(layout_grid, row, label, self._move(widget, layout_card), FIELD_HINTS[label])
        file_fields = (
            ("Beside input", s.beside_input), ("Use folder", s.folder_output),
            ("Subtitle file", s.output_srt), ("Format", s.subtitle_format),
            ("Transcript (TXT)", s.output_txt), ("Extracted audio", s.output_audio),
            ("Video", s.output_video), ("Video mode", s.video_mode),
            ("Save original", s.output_source_subtitles),
            ("Save intermediate", s.output_intermediate_subtitles), ("Overwrite", s.overwrite),
        )
        for row, (label, widget) in enumerate(file_fields, 1):
            self._row(files_grid, row, label, self._move(widget, files_card), FIELD_HINTS[label])
        output_row = QHBoxLayout()
        output_row.addWidget(self._move(s.output_dir, files_card), 1)
        output_row.addWidget(self._move(s.browse_output_dir, files_card))
        output_label = QLabel("Output folder")
        output_label.setObjectName("macFieldLabel")
        files_grid.addWidget(output_label, 12, 0)
        files_grid.addLayout(output_row, 12, 1)
        output_hint = QLabel(s.output_dir.accessibleDescription())
        output_hint.setObjectName("macFieldHint")
        output_hint.setWordWrap(True)
        files_grid.addWidget(output_hint, 12, 2)
        system_fields = (
            ("Offline", s.offline), ("Retry translation", s.retry_translation),
            ("Verbose", s.verbose), ("Extract workers", s.extract_workers),
        )
        for row, (label, widget) in enumerate(system_fields, 1):
            self._row(system_grid, row, label, self._move(widget, system_card), FIELD_HINTS[label])
        cache_row = QHBoxLayout()
        cache_row.addWidget(self._move(s.translation_cache_dir, system_card), 1)
        cache_row.addWidget(self._move(s.browse_cache_dir, system_card))
        cache_label = QLabel("Cache folder")
        cache_label.setObjectName("macFieldLabel")
        system_grid.addWidget(cache_label, 5, 0)
        system_grid.addLayout(cache_row, 5, 1)
        cache_hint = QLabel(s.translation_cache_dir.accessibleDescription())
        cache_hint.setObjectName("macFieldHint")
        cache_hint.setWordWrap(True)
        system_grid.addWidget(cache_hint, 5, 2)
        system_grid.addWidget(self._move(s.clear_cache, system_card), 6, 1, 1, 2, Qt.AlignmentFlag.AlignLeft)

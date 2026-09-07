from __future__ import annotations

import json
import os
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..model_manager import LANGUAGES, list_models, normalize_language, source_dir
from ..processing import default_processing_values


ASR_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo", "distil-large-v3.5")
ASR_LANGUAGES = (
    ("", "Auto detect"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
)
COMPUTE_TYPES = ("auto", "float16", "int8", "int8_float16", "float32")
TARGET_LANGUAGES = tuple((code, name) for code, name in LANGUAGES.items())
OPTION_KEYS = (
    "backend",
    "model",
    "output_dir",
    "output_srt",
    "output_audio",
    "output_video",
    "subtitle_format",
    "output_txt",
    "output_mkv",
    "language",
    "translate_to",
    "translation_engine",
    "translation_route",
    "translation_model",
    "translation_device",
    "translation_cache_dir",
    "retry_translation",
    "offline",
    "bilingual",
    "output_source_subtitles",
    "output_intermediate_subtitles",
    "subtitle_layout",
    "no_adaptive_layout",
    "extract_workers",
    "device",
    "compute_type",
    "inference_batch_size",
    "vad",
    "verbose",
    "enhance_consistency",
    "word_timestamps",
    "max_chars_per_line",
    "max_lines",
    "max_cps",
    "min_duration",
    "max_duration",
    "context",
    "glossary",
    "overwrite",
)


class SettingsPanel(QWidget):
    changed = Signal()
    clear_cache_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._restoring = False
        self._last_model_url: str | None = None
        self._translation_model_invalid = False
        self.resize(420, 550)
        self.setMinimumWidth(0)
        self.setMinimumHeight(250)

        self.tabs = QTabWidget(self)
        self.tabs.setMinimumWidth(0)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.setStyleSheet("QTabBar::tab { min-width: 0px; padding: 9px 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.tabs)

        self._build_speech_tab()
        self._build_translate_tab()
        self._build_layout_tab()
        self._build_files_tab()
        self._build_system_tab()
        self._connect_changes()
        self._sync_controls()

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend": self.backend.currentText(),
            "model": self.model.currentText().strip(),
            "language": self._combo_data(self.language),
            "device": self._combo_data(self.device),
            "compute_type": self.compute_type.currentText(),
            "inference_batch_size": self.inference_batch_size.value(),
            "vad": self.vad.isChecked(),
            "word_timestamps": self.word_timestamps.isChecked(),
            "enhance_consistency": self.enhance_consistency.isChecked(),
            "translate_enabled": self.translate_enabled.isChecked(),
            "translate_to": self._combo_data(self.translate_to),
            "translation_engine": self.translation_engine.currentText(),
            "translation_route": self.translation_route.currentText(),
            "translation_model": self.translation_model.currentText().strip() or None,
            "translation_device": self.translation_device.currentText(),
            "bilingual": self.bilingual.isChecked(),
            "context": self.context.toPlainText(),
            "glossary_text": self.glossary.toPlainText(),
            "subtitle_layout": self.subtitle_layout.currentText(),
            "max_chars_per_line": self.max_chars_per_line.value(),
            "max_lines": self.max_lines.value(),
            "max_cps": self.max_cps.value(),
            "min_duration": self.min_duration.value(),
            "max_duration": self.max_duration.value(),
            "output_location": "beside_input" if self.beside_input.isChecked() else "folder",
            "output_dir": self.output_dir.currentText().strip() or None,
            "output_srt": self.output_srt.isChecked(),
            "subtitle_format": self.subtitle_format.currentText(),
            "output_txt": self.output_txt.isChecked(),
            "output_audio": self.output_audio.isChecked(),
            "output_video": self.output_video.isChecked(),
            "video_mode": self.video_mode.currentText(),
            "output_source_subtitles": self.output_source_subtitles.isChecked(),
            "output_intermediate_subtitles": self.output_intermediate_subtitles.isChecked(),
            "overwrite": self.overwrite.isChecked(),
            "offline": self.offline.isChecked(),
            "retry_translation": self.retry_translation.isChecked(),
            "verbose": self.verbose.isChecked(),
            "extract_workers": self.extract_workers.value(),
            "translation_cache_dir": self.translation_cache_dir.currentText().strip() or None,
        }

    def restore(self, settings: dict[str, Any]) -> None:
        self._restoring = True
        try:
            self._set_combo_text(self.backend, settings.get("backend", "stable"))
            self._set_combo_text(self.model, settings.get("model", "small"))
            self._set_combo_data(self.language, settings.get("language"))
            self._set_combo_data(self.device, settings.get("device"))
            self._set_combo_text(self.compute_type, settings.get("compute_type", "auto"))
            self.inference_batch_size.setValue(int(settings.get("inference_batch_size", 1)))
            self.vad.setChecked(bool(settings.get("vad", False)))
            self.word_timestamps.setChecked(bool(settings.get("word_timestamps", False)))
            self.enhance_consistency.setChecked(bool(settings.get("enhance_consistency", False)))
            self.translate_enabled.setChecked(bool(settings.get("translate_enabled", settings.get("translate_to") is not None)))
            self._set_combo_data(self.translate_to, settings.get("translate_to"))
            self._set_combo_text(self.translation_engine, settings.get("translation_engine", "local"))
            self._set_combo_text(self.translation_route, settings.get("translation_route", "direct"))
            self._set_combo_text(self.translation_device, settings.get("translation_device", "auto"))
            self.bilingual.setChecked(bool(settings.get("bilingual", False)))
            self.context.setPlainText(str(settings.get("context", "")))
            glossary_text = settings.get("glossary_text")
            if glossary_text is None and settings.get("glossary") is not None:
                glossary_text = json.dumps(settings["glossary"], ensure_ascii=False, indent=2)
            self.glossary.setPlainText(str(glossary_text or ""))
            self._set_combo_text(self.subtitle_layout, settings.get("subtitle_layout", "adaptive"))
            self.max_chars_per_line.setValue(int(settings.get("max_chars_per_line", 42)))
            self.max_lines.setValue(int(settings.get("max_lines", 2)))
            self.max_cps.setValue(float(settings.get("max_cps", 17.0)))
            self.min_duration.setValue(float(settings.get("min_duration", 1.0)))
            self.max_duration.setValue(float(settings.get("max_duration", 7.0)))
            output_dir = settings.get("output_dir")
            self.beside_input.setChecked(settings.get("output_location", "beside_input") == "beside_input" or output_dir is None)
            self.folder_output.setChecked(not self.beside_input.isChecked())
            self._set_combo_text(self.output_dir, output_dir or "")
            self.output_srt.setChecked(bool(settings.get("output_srt", True)))
            self._set_combo_text(self.subtitle_format, settings.get("subtitle_format", "srt"))
            self.output_txt.setChecked(bool(settings.get("output_txt", False)))
            self.output_audio.setChecked(bool(settings.get("output_audio", False)))
            self.output_video.setChecked(bool(settings.get("output_video", False)))
            self._set_combo_text(self.video_mode, "MKV soft" if settings.get("output_mkv") else settings.get("video_mode", "MP4 burn"))
            self.output_source_subtitles.setChecked(bool(settings.get("output_source_subtitles", False)))
            self.output_intermediate_subtitles.setChecked(bool(settings.get("output_intermediate_subtitles", False)))
            self.overwrite.setChecked(bool(settings.get("overwrite", False)))
            self.offline.setChecked(bool(settings.get("offline", False)))
            self.retry_translation.setChecked(bool(settings.get("retry_translation", False)))
            self.verbose.setChecked(bool(settings.get("verbose", False)))
            self.extract_workers.setValue(int(settings.get("extract_workers", self.extract_workers.value())))
            self._set_combo_text(self.translation_cache_dir, settings.get("translation_cache_dir") or "")
            self._refresh_translation_models(settings.get("translation_model"))
        finally:
            self._restoring = False
        self._sync_controls()

    def options(self) -> dict[str, Any]:
        values = default_processing_values()
        values.update({key: None for key in values if key not in OPTION_KEYS})
        snap = self.snapshot()
        translate = bool(snap["translate_enabled"])
        glossary = self._parse_glossary() if translate else None

        values.update(
            {
                "backend": snap["backend"],
                "model": snap["model"],
                "language": snap["language"],
                "device": None if snap["device"] == "auto" else snap["device"],
                "compute_type": snap["compute_type"] if snap["backend"] == "faster" else "auto",
                "inference_batch_size": snap["inference_batch_size"] if snap["backend"] == "faster" else 1,
                "vad": snap["vad"] if snap["backend"] == "faster" else False,
                "word_timestamps": snap["word_timestamps"],
                "enhance_consistency": snap["enhance_consistency"],
                "translate_to": snap["translate_to"] if translate else None,
                "translation_engine": snap["translation_engine"],
                "translation_route": snap["translation_route"] if translate else "direct",
                "translation_model": snap["translation_model"] if translate else None,
                "translation_device": snap["translation_device"],
                "bilingual": snap["bilingual"] if translate else False,
                "context": snap["context"].strip() if translate else "",
                "glossary": glossary if translate else None,
                "subtitle_layout": snap["subtitle_layout"],
                "no_adaptive_layout": snap["subtitle_layout"] == "preserve",
                "max_chars_per_line": snap["max_chars_per_line"],
                "max_lines": snap["max_lines"],
                "max_cps": float(snap["max_cps"]),
                "min_duration": float(snap["min_duration"]),
                "max_duration": float(snap["max_duration"]),
                "output_dir": None if snap["output_location"] == "beside_input" else snap["output_dir"],
                "output_srt": snap["output_srt"],
                "subtitle_format": snap["subtitle_format"].lower(),
                "output_txt": snap["output_txt"] if translate else snap["output_txt"],
                "output_audio": snap["output_audio"],
                "output_video": snap["output_video"],
                "output_mkv": snap["video_mode"] == "MKV soft",
                "output_source_subtitles": snap["output_source_subtitles"] if translate else False,
                "output_intermediate_subtitles": snap["output_intermediate_subtitles"] if translate else False,
                "overwrite": snap["overwrite"],
                "offline": snap["offline"],
                "retry_translation": snap["retry_translation"],
                "verbose": snap["verbose"],
                "extract_workers": snap["extract_workers"],
                "translation_cache_dir": snap["translation_cache_dir"],
            }
        )

        self._validate_options(values, translate)
        return {key: values[key] for key in OPTION_KEYS}

    def _build_speech_tab(self) -> None:
        form = self._form_tab("Speech")
        self.backend = self._combo(("stable", "faster"))
        self.model = self._combo(ASR_MODELS, editable=True)
        self.model.setCurrentText("small")
        self.language = self._data_combo(ASR_LANGUAGES, editable=True)
        self.device = self._data_combo((("auto", "Auto"), ("cpu", "CPU"), ("cuda", "CUDA")))
        self.compute_type = self._combo(COMPUTE_TYPES)
        self.inference_batch_size = self._spin(1, 64, 1)
        self.vad = QCheckBox()
        self.word_timestamps = QCheckBox()
        self.enhance_consistency = QCheckBox()
        self.asr_validation = QLabel("")
        form.addRow("Backend", self.backend)
        form.addRow("Model", self.model)
        form.addRow("Audio language", self.language)
        form.addRow("Device", self.device)
        form.addRow("Compute type", self.compute_type)
        form.addRow("Inference batch", self.inference_batch_size)
        form.addRow("VAD", self.vad)
        form.addRow("Word timestamps", self.word_timestamps)
        form.addRow("Enhance consistency", self.enhance_consistency)
        form.addRow("", self.asr_validation)

    def _build_translate_tab(self) -> None:
        form = self._form_tab("Translate")
        self.translate_enabled = QCheckBox()
        self.translate_to = self._data_combo(TARGET_LANGUAGES)
        self.translation_engine = self._combo(("local", "google"))
        self.translation_route = self._combo(("direct", "via-en"))
        self.translation_model = self._combo((), editable=True)
        self.translation_device = self._combo(("auto", "cpu", "cuda"))
        self.bilingual = QCheckBox()
        self.context = QPlainTextEdit()
        self.context.setFixedHeight(72)
        self.context.setMinimumWidth(0)
        self.context.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.glossary = QPlainTextEdit()
        self.glossary.setFixedHeight(72)
        self.glossary.setMinimumWidth(0)
        self.glossary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.route_preview = QLabel("")
        self.route_preview.setWordWrap(True)
        self.route_preview.setMinimumWidth(0)
        self.route_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.model_info = QLabel("")
        self.model_info.setWordWrap(True)
        self.model_info.setMinimumWidth(0)
        self.model_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.open_model = QPushButton("Open model")
        self.open_model.setIcon(QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_DirLinkIcon))
        self.open_model.clicked.connect(self._open_model_url)
        form.addRow("Translate", self.translate_enabled)
        form.addRow("Target language", self.translate_to)
        form.addRow("Provider", self.translation_engine)
        form.addRow("Route", self.translation_route)
        form.addRow("Model", self.translation_model)
        form.addRow("Translation device", self.translation_device)
        form.addRow("Bilingual", self.bilingual)
        form.addRow("Context", self.context)
        form.addRow("Glossary JSON", self.glossary)
        form.addRow("Route preview", self.route_preview)
        form.addRow("Model info", self.model_info)
        form.addRow("", self.open_model)

    def _build_layout_tab(self) -> None:
        form = self._form_tab("Layout")
        self.subtitle_layout = self._combo(("adaptive", "preserve"))
        self.max_chars_per_line = self._spin(1, 200, 42)
        self.max_lines = self._spin(1, 10, 2)
        self.max_cps = self._double_spin(1.0, 80.0, 17.0)
        self.min_duration = self._double_spin(0.1, 60.0, 1.0)
        self.max_duration = self._double_spin(0.1, 60.0, 7.0)
        form.addRow("Layout", self.subtitle_layout)
        form.addRow("Max chars per line", self.max_chars_per_line)
        form.addRow("Max lines", self.max_lines)
        form.addRow("Max CPS", self.max_cps)
        form.addRow("Min duration", self.min_duration)
        form.addRow("Max duration", self.max_duration)

    def _build_files_tab(self) -> None:
        form = self._form_tab("Files")
        self.beside_input = QCheckBox("Beside input")
        self.beside_input.setChecked(True)
        self.folder_output = QCheckBox("Folder")
        self.output_dir = self._combo((), editable=True)
        self.browse_output_dir = self._browse_button()
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(self.output_dir)
        output_row.addWidget(self.browse_output_dir)
        self.output_srt = QCheckBox()
        self.output_srt.setChecked(True)
        self.subtitle_format = self._combo(("SRT", "VTT"))
        self.output_txt = QCheckBox()
        self.output_audio = QCheckBox()
        self.output_video = QCheckBox()
        self.video_mode = self._combo(("MP4 burn", "MKV soft"))
        self.output_source_subtitles = QCheckBox()
        self.output_intermediate_subtitles = QCheckBox()
        self.overwrite = QCheckBox()
        form.addRow("Beside input", self.beside_input)
        form.addRow("Use folder", self.folder_output)
        form.addRow("Output folder", output_row)
        form.addRow("Subtitle file", self.output_srt)
        form.addRow("Format", self.subtitle_format)
        form.addRow("Text file", self.output_txt)
        form.addRow("Extracted audio", self.output_audio)
        form.addRow("Video", self.output_video)
        form.addRow("Video mode", self.video_mode)
        form.addRow("Save original", self.output_source_subtitles)
        form.addRow("Save intermediate", self.output_intermediate_subtitles)
        form.addRow("Overwrite", self.overwrite)
        self.beside_input.toggled.connect(self._on_output_location_changed)
        self.folder_output.toggled.connect(self._on_output_location_changed)
        self.browse_output_dir.clicked.connect(lambda: self._browse_directory(self.output_dir))

    def _build_system_tab(self) -> None:
        form = self._form_tab("System")
        self.offline = QCheckBox()
        self.retry_translation = QCheckBox()
        self.verbose = QCheckBox()
        self.extract_workers = self._spin(1, 64, max(1, (os.cpu_count() or 2) // 2))
        self.translation_cache_dir = self._combo((), editable=True)
        self.browse_cache_dir = self._browse_button()
        cache_row = QHBoxLayout()
        cache_row.setContentsMargins(0, 0, 0, 0)
        cache_row.addWidget(self.translation_cache_dir)
        cache_row.addWidget(self.browse_cache_dir)
        self.clear_cache = QPushButton("Clear cache")
        self.clear_cache.setIcon(QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_TrashIcon))
        form.addRow("Offline", self.offline)
        form.addRow("Retry translation", self.retry_translation)
        form.addRow("Verbose", self.verbose)
        form.addRow("Extract workers", self.extract_workers)
        form.addRow("Cache folder", cache_row)
        form.addRow("", self.clear_cache)
        self.browse_cache_dir.clicked.connect(lambda: self._browse_directory(self.translation_cache_dir))
        self.clear_cache.clicked.connect(self.clear_cache_requested.emit)

    def _form_tab(self, title: str) -> QFormLayout:
        page = QWidget()
        page.setMinimumWidth(0)
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(form.labelAlignment())
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        scroll = QScrollArea()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setMinimumWidth(0)
        self.tabs.addTab(scroll, title)
        return form

    def _connect_changes(self) -> None:
        widgets = (
            self.backend,
            self.model,
            self.language,
            self.device,
            self.compute_type,
            self.inference_batch_size,
            self.vad,
            self.word_timestamps,
            self.enhance_consistency,
            self.translate_enabled,
            self.translate_to,
            self.translation_engine,
            self.translation_route,
            self.translation_model,
            self.translation_device,
            self.bilingual,
            self.subtitle_layout,
            self.max_chars_per_line,
            self.max_lines,
            self.max_cps,
            self.min_duration,
            self.max_duration,
            self.beside_input,
            self.folder_output,
            self.output_dir,
            self.output_srt,
            self.subtitle_format,
            self.output_txt,
            self.output_audio,
            self.output_video,
            self.video_mode,
            self.output_source_subtitles,
            self.output_intermediate_subtitles,
            self.overwrite,
            self.offline,
            self.retry_translation,
            self.verbose,
            self.extract_workers,
            self.translation_cache_dir,
        )
        for widget in widgets:
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_changed)
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self._on_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_changed)
        self.context.textChanged.connect(self._on_changed)
        self.glossary.textChanged.connect(self._on_changed)

    def _on_changed(self) -> None:
        if self._restoring:
            return
        self._sync_controls()
        self.changed.emit()

    def _on_output_location_changed(self, checked: bool) -> None:
        if self._restoring or not checked:
            return
        sender = self.sender()
        self.beside_input.blockSignals(True)
        self.folder_output.blockSignals(True)
        self.beside_input.setChecked(sender is self.beside_input)
        self.folder_output.setChecked(sender is self.folder_output)
        self.beside_input.blockSignals(False)
        self.folder_output.blockSignals(False)
        self._on_changed()

    def _sync_controls(self) -> None:
        faster = self.backend.currentText() == "faster"
        for widget in (self.compute_type, self.inference_batch_size, self.vad):
            widget.setEnabled(faster)
        self.asr_validation.setText(
            "distil-large-v3.5 requires faster backend" if self.model.currentText().strip() == "distil-large-v3.5" and not faster else ""
        )

        translating = self.translate_enabled.isChecked()
        local = self.translation_engine.currentText() == "local"
        for widget in (
            self.translate_to,
            self.translation_engine,
            self.translation_route,
            self.bilingual,
            self.context,
            self.glossary,
            self.output_source_subtitles,
            self.output_intermediate_subtitles,
        ):
            widget.setEnabled(translating)
        for widget in (self.translation_model, self.translation_device, self.open_model):
            widget.setEnabled(translating and local)
        self._refresh_translation_models(self.translation_model.currentText().strip() or None)

        beside = self.beside_input.isChecked()
        self.folder_output.blockSignals(True)
        self.folder_output.setChecked(not beside)
        self.folder_output.blockSignals(False)
        self.output_dir.setEnabled(not beside)
        self.browse_output_dir.setEnabled(not beside)
        self.video_mode.setEnabled(self.output_video.isChecked())

    def _refresh_translation_models(self, preferred: str | None = None) -> None:
        source = self._combo_data(self.language)
        target = self._combo_data(self.translate_to)
        route = self.translation_route.currentText()
        current = preferred if preferred is not None else self.translation_model.currentText().strip()
        models = self._available_translation_model_ids(source, target, route)
        self.translation_model.blockSignals(True)
        self.translation_model.clear()
        self.translation_model.addItems(models)
        if current:
            self._set_combo_text(self.translation_model, current)
        elif models:
            self.translation_model.setCurrentIndex(0)
        self.translation_model.blockSignals(False)
        self._update_translation_info(models)

    def _available_translation_model_ids(self, source: str | None, target: str | None, route: str) -> list[str]:
        if route == "via-en":
            models = ["opus-mt"]
        else:
            models = []
        try:
            models.extend(spec.id for spec in list_models(source, target, route=route))
        except Exception:
            return models
        return list(dict.fromkeys(models))

    def _update_translation_info(self, valid_models: list[str]) -> None:
        source = self._combo_data(self.language) or "detected"
        target = self._combo_data(self.translate_to) or "?"
        route = self.translation_route.currentText()
        model_id = self.translation_model.currentText().strip()
        self.route_preview.setText(f"{source} -> en -> {target}" if route == "via-en" else f"{source} -> {target}")
        self._translation_model_invalid = bool(model_id) and model_id not in valid_models
        if self._translation_model_invalid:
            self._last_model_url = None
            self.model_info.setToolTip(model_id)
            self.model_info.setText("Invalid model for current language/route.")
            return
        if not model_id or model_id == "opus-mt":
            self._last_model_url = None
            self.model_info.setText("OPUS-MT resolves to the validated language pair models.")
            return
        try:
            spec = next(spec for spec in list_models(None, None) if spec.id == model_id)
        except Exception as error:
            self._last_model_url = None
            self.model_info.setText(str(error))
            return
        self._last_model_url = spec.url
        size_mib = spec.download_size / (1024 * 1024)
        context_mode = "dialogue" if spec.contextual else "sentence"
        cache_state = "Cached files (verified at start)" if self._source_files_present(spec) else "Download required"
        notice = " Noncommercial research model." if "nc" in spec.license else ""
        self.model_info.setText(
            f"{size_mib:.1f} MiB, license {spec.license}, {context_mode} context. {cache_state}.{notice}"
        )

    def _source_files_present(self, spec: Any) -> bool:
        directory = source_dir(spec, self.translation_cache_dir.currentText().strip() or None)
        return all((directory / item.name).is_file() for item in spec.files)

    def _validate_options(self, values: dict[str, Any], translate: bool) -> None:
        if values["backend"] == "stable" and values["model"] == "distil-large-v3.5":
            raise ValueError("distil-large-v3.5 requires faster backend")
        if values["backend"] == "faster" and values["inference_batch_size"] > 1 and not values["vad"]:
            raise ValueError("faster inference batch > 1 requires VAD")
        if values["max_duration"] < values["min_duration"]:
            raise ValueError("max_duration must be greater than or equal to min_duration")
        if not translate:
            return
        if not values["translate_to"]:
            raise ValueError("Translation requires a target language")
        if values["translation_engine"] == "google" and values["offline"]:
            raise ValueError("Offline mode cannot use Google translation")
        if values["translation_route"] == "via-en":
            if self._is_english(values["language"]) or self._is_english(values["translate_to"]):
                raise ValueError("via-en requires non-English source and target languages")
        if values["translation_engine"] == "local":
            target = normalize_language(values["translate_to"])
            source = normalize_language(values["language"]) if values["language"] else None
            valid = self._available_translation_model_ids(source, target, values["translation_route"])
            if values["translation_model"] and values["translation_model"] not in valid:
                raise ValueError("Selected translation model is invalid for the current language and route")

    def _parse_glossary(self) -> dict[str, str] | None:
        text = self.glossary.toPlainText().strip()
        if not text:
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Glossary JSON must be an object")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
            raise ValueError("Glossary JSON terms and values must be strings")
        return data

    def _browse_directory(self, combo: QComboBox) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose folder", combo.currentText().strip() or os.getcwd())
        if directory:
            self._set_combo_text(combo, directory)

    def _open_model_url(self) -> None:
        if self._last_model_url:
            QDesktopServices.openUrl(QUrl(self._last_model_url))

    @staticmethod
    def _combo(items: tuple[str, ...], editable: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(editable)
        combo.addItems(items)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if combo.lineEdit() is not None:
            combo.lineEdit().setMinimumWidth(0)
            combo.lineEdit().setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return combo

    @staticmethod
    def _data_combo(items: tuple[tuple[str, str], ...], editable: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(editable)
        for value, label in items:
            combo.addItem(label, value)
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if combo.lineEdit() is not None:
            combo.lineEdit().setMinimumWidth(0)
            combo.lineEdit().setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return combo

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setMinimumWidth(0)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    @staticmethod
    def _double_spin(minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(1)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        spin.setMinimumWidth(0)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return spin

    @staticmethod
    def _browse_button() -> QPushButton:
        button = QPushButton()
        button.setIcon(QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_DirOpenIcon))
        button.setFixedWidth(32)
        return button

    @staticmethod
    def _combo_data(combo: QComboBox) -> str | None:
        text = combo.currentText().strip()
        if combo.isEditable() and combo.findText(text) < 0:
            return text or None
        data = combo.currentData()
        value = data if data is not None else text
        value = str(value).strip()
        return value or None

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: Any) -> None:
        text = "" if value is None else str(value)
        index = combo.findText(text)
        if index < 0:
            for item in range(combo.count()):
                if combo.itemText(item).lower() == text.lower():
                    index = item
                    break
        if index < 0 and combo.isEditable():
            combo.addItem(text)
            index = combo.findText(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(text)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        normalized = "" if value is None else str(value)
        index = combo.findData(normalized)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.addItem(normalized, normalized)
            combo.setCurrentIndex(combo.findData(normalized))

    @staticmethod
    def _is_english(language: str | None) -> bool:
        return language is not None and language.strip().lower().replace("_", "-") in ("en", "english")

"""Mac workspace colors, scoped to the redesigned window."""

LIGHT_STYLE = """
QWidget#macRoot { background: #f2f5ff; color: #15213d; }
QFrame#macSidebar { background: #e7edff; border-right: 1px solid #d6dff7; }
QPushButton#macNav { text-align: left; padding: 8px 12px; border: 0; border-radius: 8px; background: transparent; color: #25365d; font-size: 12px; }
QPushButton#macNav:checked { background: #4c78f3; color: white; font-weight: 600; }
QPushButton#macNav:hover:!checked { background: #d8e3ff; }
QFrame#macCard, QWidget#macCard { background: #ffffff; border: 1px solid #d8e0f2; border-radius: 11px; }
QFrame#macSideCard, QWidget#macSideCard { background: #e9ecff; border: 0; border-radius: 9px; }
QFrame#macStatusCard, QWidget#macStatusCard { background: #ffffff; border: 1px solid #d8e0f2; border-radius: 10px; }
QFrame#macCompletedCard, QWidget#macCompletedCard { background: #ffffff; border: 1px solid #d8e0f2; border-radius: 9px; }
QLabel#appTitle { font-size: 18px; font-weight: 700; color: #15213d; }
QLabel#macPageTitle, QLabel#sectionTitle { font-size: 20px; font-weight: 700; color: #15213d; }
QLabel#macPageSubtitle, QLabel#sectionDescription { font-size: 12px; color: #536484; }
QLabel#macFieldHint { font-size: 12px; color: #526282; }
QLabel#macFieldLabel { color: #15213d; font-size: 12px; font-weight: 400; }
QLabel#macCardTitle { font-size: 14px; font-weight: 650; color: #24345b; }
QLabel#muted, QLabel#macQueueSummary { color: #526282; }
QLineEdit { color: #15213d; selection-background-color: #dce6ff; }
QLineEdit::placeholder { color: #6a7894; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #a9b9dc; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #3f70ed; border-color: #3f70ed; }
QCheckBox { color: #40506c; font-size: 12px; font-weight: 400; }
QPushButton, QToolButton { background: white; color: #25365d; border: 1px solid #cbd6ed; border-radius: 6px; padding: 5px 12px; }
QPushButton:hover, QToolButton:hover { background: #edf3ff; border-color: #9eb4ea; }
QPushButton:pressed, QToolButton:pressed { background: #dfe9ff; border-color: #7194ed; }
QPushButton:disabled, QToolButton:disabled { color: #8e9ab3; background: #f5f7fc; border-color: #e0e6f2; }
QPushButton:focus, QToolButton:focus { border-color: #6d91ef; }
QPushButton#macSettingsNav { text-align: left; padding: 8px 12px; border: 0; border-radius: 6px; background: transparent; }
QPushButton#macSettingsNav:checked { color: white; background: #4c78f3; }
QLabel#macSideTitle { color: #4938cd; font-weight: 600; }
QLabel#sectionGuidance { background: #e9ecff; border-radius: 9px; color: #344774; padding: 14px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit { border: 1px solid #cbd6ed; border-radius: 6px; padding: 4px 7px; background: white; }
QTableView, QTableWidget, QListWidget { border: 1px solid #d8e0f2; border-radius: 7px; background: white; alternate-background-color: #f5f7ff; selection-background-color: #dce6ff; }
QPushButton#primary, QPushButton#macToolbarButton { background: #3768ed; color: white; border: 1px solid #2858d7; border-radius: 6px; padding: 6px 14px; font-weight: 600; }
QPushButton#primary:hover, QPushButton#macToolbarButton:hover { background: #2859df; border-color: #244fc3; }
QPushButton#primary:pressed, QPushButton#macToolbarButton:pressed { background: #214bc4; }
QPushButton#primary:disabled { background: #b5c4e7; border-color: #aab7d6; }
QPushButton#macToolbarButton:disabled { background: #b5c4e7; border-color: #aab7d6; }
QPushButton#secondary { background: white; color: #25365d; border: 1px solid #cbd6ed; border-radius: 6px; padding: 6px 12px; }
QPushButton#secondary:hover { background: #edf3ff; border-color: #9eb4ea; }
QToolButton#macIconButton { background: white; border: 1px solid #cbd6ed; border-radius: 6px; padding: 5px; }
QToolButton#macIconButton:hover { background: #edf3ff; border-color: #9eb4ea; }
QToolButton#macIconButton:pressed { background: #dfe9ff; }
QProgressBar { border: 0; border-radius: 4px; background: #e3eaf7; text-align: center; color: #14223d; }
QProgressBar::chunk { border-radius: 4px; background: #4d74ee; }
QHeaderView::section { background: #f8faff; border: 0; border-right: 1px solid #d8e0f2; border-bottom: 1px solid #d8e0f2; padding: 4px 8px; color: #25365d; }
QPushButton#macDropZone, QPushButton#dropZone { border: 1px dashed #afbfeb; border-radius: 8px; background: #f8faff; color: #41577d; padding: 15px; }
QPushButton#macDropZone:hover, QPushButton#dropZone:hover { background: #eef3ff; }
"""

DARK_STYLE = """
QWidget#macRoot { background: #202738; }
QFrame#macSidebar { background: #27324e; border-right: 1px solid #3a4664; }
QPushButton#macNav { text-align: left; padding: 9px 12px; border: 0; border-radius: 8px; background: transparent; color: #e4ebff; }
QPushButton#macNav:checked { background: #4c78f3; color: white; font-weight: 600; }
QPushButton#macNav:hover:!checked { background: #354462; }
QFrame#macCard, QWidget#macCard, QFrame#macStatusCard, QWidget#macStatusCard, QFrame#macCompletedCard, QWidget#macCompletedCard { background: #293246; border: 1px solid #3d4963; border-radius: 10px; }
QFrame#macSideCard, QWidget#macSideCard { background: #343c63; border-radius: 9px; }
QLabel#appTitle, QLabel#macPageTitle, QLabel#sectionTitle { color: #f1f4ff; font-weight: 700; }
QLabel#macPageSubtitle, QLabel#sectionDescription, QLabel#macFieldHint { color: #b8c5e3; }
QLabel#macSideTitle { color: #b2a7ff; font-weight: 600; }
QLabel#sectionGuidance { background: #313b5b; border-radius: 9px; color: #e3eaff; padding: 14px; }
"""

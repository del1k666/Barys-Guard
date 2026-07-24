STYLE = """
QMainWindow, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', 'Arial', sans-serif;
    font-size: 13px;
}
QTabWidget::pane {
    border: 1px solid #21262d;
    background-color: #161b22;
    border-radius: 8px;
}
QTabBar::tab {
    background-color: #0d1117;
    color: #8b949e;
    padding: 10px 20px;
    border: 1px solid #21262d;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.3px;
}
QTabBar::tab:selected {
    background-color: #161b22;
    color: #e6edf3;
    border-bottom: 2px solid #58a6ff;
}
QTabBar::tab:hover { color: #e6edf3; background-color: #1c2128; }
QLineEdit {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 12px;
    color: #e6edf3;
    font-size: 13px;
    font-family: 'Segoe UI', sans-serif;
}
QLineEdit:focus { border: 1px solid #388bfd; }
QLineEdit::placeholder { color: #484f58; }
QPushButton {
    background-color: #1a7f37;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'Segoe UI', sans-serif;
    letter-spacing: 0.2px;
}
QPushButton:hover    { background-color: #2ea043; }
QPushButton:pressed  { background-color: #116329; }
QPushButton:disabled { background-color: #21262d; color: #484f58; }
QPushButton#dangerBtn  { background-color: #b91c1c; }
QPushButton#dangerBtn:hover { background-color: #dc2626; }
QPushButton#secondaryBtn {
    background-color: #21262d;
    border: 1px solid #30363d;
    color: #c9d1d9;
}
QPushButton#secondaryBtn:hover { background-color: #2d333b; border-color: #484f58; }
QPushButton#accentBtn { background-color: #1d4ed8; }
QPushButton#accentBtn:hover { background-color: #2563eb; }
QTextEdit {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 6px;
    color: #e6edf3;
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 12px;
    padding: 10px;
    line-height: 1.5;
}
QTableWidget {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 8px;
    gridline-color: #161b22;
    color: #e6edf3;
    font-size: 12px;
    font-family: 'Segoe UI', sans-serif;
}
QTableWidget::item            { padding: 7px 10px; border-bottom: 1px solid #161b22; }
QTableWidget::item:selected   { background-color: #1c3a5e; color: #79c0ff; }
QHeaderView::section {
    background-color: #161b22;
    color: #6e7681;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid #21262d;
    border-bottom: 1px solid #21262d;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
QProgressBar {
    background-color: #21262d;
    border: none;
    border-radius: 3px;
    height: 4px;
}
QProgressBar::chunk { background-color: #388bfd; border-radius: 3px; }
QGroupBox {
    border: 1px solid #21262d;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: #6e7681;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #388bfd;
    text-transform: uppercase;
}
QScrollBar:vertical   { background: transparent; width: 6px; }
QScrollBar::handle:vertical { background: #30363d; border-radius: 3px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #484f58; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QListWidget {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 6px;
    color: #e6edf3;
    font-size: 12px;
    padding: 4px;
    outline: none;
}
QListWidget::item { padding: 5px 8px; border-radius: 4px; }
QListWidget::item:hover    { background-color: #1c2128; }
QListWidget::item:selected { background-color: #1c3a5e; color: #79c0ff; }
QComboBox {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 7px 12px;
    color: #e6edf3;
    font-size: 12px;
    font-family: 'Segoe UI', sans-serif;
}
QComboBox:hover { border-color: #484f58; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    selection-background-color: #1c3a5e;
    color: #e6edf3;
    padding: 4px;
}
QCheckBox { spacing: 8px; color: #c9d1d9; font-size: 12px; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    background-color: #161b22;
    border: 1px solid #484f58;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #1a7f37;
    border-color: #1a7f37;
}
QLabel { color: #c9d1d9; }
"""

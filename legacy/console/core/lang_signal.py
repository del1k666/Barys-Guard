from PyQt6.QtCore import QObject, pyqtSignal


class _LangSignal(QObject):
    changed = pyqtSignal(str)   # emits new language code e.g. "eng"


lang_signal = _LangSignal()

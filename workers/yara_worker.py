from PyQt6.QtCore import QThread, pyqtSignal
from core.yara_engine import run_yara_scan


class YARAWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)
    def __init__(self, rules_dict, target_path, results_dir):
        super().__init__()
        self.rules_dict  = rules_dict
        self.target_path = target_path
        self.results_dir = results_dir
    def run(self):
        try:
            matches = run_yara_scan(self.rules_dict, self.target_path, self.results_dir)
            self.done.emit(matches)
        except Exception as e:
            self.error.emit(str(e))

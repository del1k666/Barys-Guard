import sys
import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from styles import STYLE
from ui.main_window import MainWindow

_ICON = str(Path(__file__).parent / "docs" / "img" / "image.png")


def main():
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        app.setApplicationName("BarysGuard")
        app.setWindowIcon(QIcon(_ICON))
        w = MainWindow()
        w.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error_log.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(err)
        print("STARTUP ERROR:", err)
        raise


if __name__ == "__main__":
    main()

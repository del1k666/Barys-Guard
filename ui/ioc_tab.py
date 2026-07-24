import os
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTextEdit, QFileDialog, QProgressBar, QFrame
)
from config import Config, get_results_dir
from workers.ioc_worker import IOCWorker
from ui.dashboard_tab import DashboardTab


class IOCTab(QWidget):
    def __init__(self):
        super().__init__(); self._build(); self._result_dir=None

    def _build(self):
        lay = QVBoxLayout(self); lay.setSpacing(10); lay.setContentsMargins(16,16,16,16)

        grp = QGroupBox("Конфигурация")
        gl  = QHBoxLayout(grp)
        gl.addWidget(QLabel("Папка результатов:"))
        self.dir_inp = QLineEdit(str(get_results_dir()))
        gl.addWidget(self.dir_inp)
        btn = QPushButton("📂"); btn.setObjectName("secondaryBtn"); btn.setFixedWidth(36)
        btn.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(self,"Выбери папку")) and self.dir_inp.setText(d))
        gl.addWidget(btn); lay.addWidget(grp)

        row = QHBoxLayout()
        self.btn_run = QPushButton("Запустить сбор IoC"); self.btn_run.setFixedHeight(38)
        self.btn_run.clicked.connect(self._run); row.addWidget(self.btn_run)
        self.btn_clr = QPushButton("Очистить"); self.btn_clr.setObjectName("secondaryBtn")
        self.btn_clr.setFixedWidth(100); self.btn_clr.clicked.connect(lambda: self.log.clear())
        row.addWidget(self.btn_clr)
        self.btn_open = QPushButton("Открыть папку"); self.btn_open.setObjectName("secondaryBtn")
        self.btn_open.setFixedWidth(130); self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open); row.addWidget(self.btn_open)
        lay.addLayout(row)

        self.prog = QProgressBar(); self.prog.setRange(0,0); self.prog.setVisible(False)
        self.prog.setFixedHeight(5); lay.addWidget(self.prog)

        # Stat cards
        sr = QHBoxLayout()
        self.sp, self._val_procs = self._card("PROCESSES","—"); sr.addWidget(self.sp)
        self.sa, self._val_auto  = self._card("AUTORUNS","—");  sr.addWidget(self.sa)
        self.sn, self._val_conn  = self._card("CONNECTIONS","—"); sr.addWidget(self.sn)
        self.ss, self._val_susp  = self._card("SUSPICIOUS","—"); sr.addWidget(self.ss)
        lay.addLayout(sr)

        grp2 = QGroupBox("Лог сбора")
        gl2  = QVBoxLayout(grp2)
        self.log = QTextEdit(); self.log.setReadOnly(True)
        gl2.addWidget(self.log); lay.addWidget(grp2)

    def _card(self, title, val):
        f = QFrame(); f.setStyleSheet("QFrame{background:#161b22;border:1px solid #30363d;border-radius:6px;}")
        fl = QVBoxLayout(f); fl.setContentsMargins(10,8,10,8)
        t = QLabel(title); t.setStyleSheet("color:#8b949e;font-size:9px;font-weight:bold;letter-spacing:2px;")
        v = QLabel(val);   v.setStyleSheet("font-size:20px;font-weight:bold;color:#58a6ff;")
        fl.addWidget(t); fl.addWidget(v)
        return f, v

    def _run(self):
        self.btn_run.setEnabled(False); self.btn_open.setEnabled(False)
        self.prog.setVisible(True); self.log.clear()
        self._w = IOCWorker(self.dir_inp.text())
        self._w.log.connect(self._append)
        self._w.done.connect(self._done)
        self._w.error.connect(self._err)
        self._w.finished.connect(lambda: (self.btn_run.setEnabled(True), self.prog.setVisible(False)))
        self._w.start()

    def _append(self, line):
        if "[SUSPICIOUS]" in line:
            self.log.append(f'<span style="color:#f85149;">{line}</span>')
        elif "[AUTORUN]" in line:
            self.log.append(f'<span style="color:#d29922;">{line}</span>')
        elif "[NET]" in line:
            self.log.append(f'<span style="color:#58a6ff;">{line}</span>')
        else:
            if "Total:" in line:
                m = re.search(r"Total:\s*(\d+).*Suspicious:\s*(\d+)", line)
                if m:
                    self._val_procs.setText(m.group(1))
                    n = int(m.group(2))
                    self._val_susp.setText(str(n))
                    self._val_susp.setStyleSheet(
                        "font-size:20px;font-weight:bold;color:" +
                        ("#f85149;" if n > 0 else "#3fb950;"))
            if "entries:" in line:
                m = re.search(r"entries:\s*(\d+)", line)
                if m:
                    self._val_auto.setText(m.group(1))
            if "connections:" in line:
                m = re.search(r"connections:\s*(\d+)", line)
                if m:
                    self._val_conn.setText(m.group(1))
            self.log.append(f'<span style="color:#8b949e;">{line}</span>')

    def _done(self, d):
        self._result_dir = d; self.btn_open.setEnabled(True)
        self.log.append('<span style="color:#3fb950;font-weight:bold;">✔ Сбор завершён!</span>')
        DashboardTab.stats["ioc_runs"] += 1
        DashboardTab.log_event("IOC", f"Сбор завершён → {d}", level="ok", scan=True, target=d)

    def _err(self, msg):
        self.log.append(f'<span style="color:#f85149;">✘ ОШИБКА: {msg}</span>')

    def _open(self):
        if self._result_dir and os.path.exists(self._result_dir):
            os.startfile(self._result_dir)

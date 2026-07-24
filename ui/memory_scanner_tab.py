import os
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QListWidget, QListWidgetItem, QCheckBox, QSplitter
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from constants import BUILTIN_YARA_RULES
from workers.process_worker import ProcessListWorker, MemScanWorker
from ui.dashboard_tab import DashboardTab


class MemoryScannerTab(QWidget):
    def __init__(self):
        super().__init__()
        self._processes   = []
        self._scan_worker = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self); lay.setSpacing(10); lay.setContentsMargins(16,16,16,16)

        # Top controls
        top = QHBoxLayout()
        self.btn_refresh = QPushButton("Обновить список процессов")
        self.btn_refresh.setObjectName("secondaryBtn")
        self.btn_refresh.setFixedHeight(34)
        self.btn_refresh.clicked.connect(self._refresh_procs)
        top.addWidget(self.btn_refresh)

        self.filter_inp = QLineEdit()
        self.filter_inp.setPlaceholderText("Фильтр по имени...")
        self.filter_inp.textChanged.connect(self._filter_procs)
        top.addWidget(self.filter_inp)

        self.chk_unsigned = QCheckBox("Только неподписанные")
        self.chk_unsigned.stateChanged.connect(self._filter_procs)
        top.addWidget(self.chk_unsigned)

        self.proc_count = QLabel("Процессов: 0")
        self.proc_count.setStyleSheet("color:#6e7681;font-size:11px;min-width:90px;")
        top.addWidget(self.proc_count)
        lay.addLayout(top)

        # Splitter: process list | scan results
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left — process list
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0); ll.setSpacing(6)

        grp_p = QGroupBox("Процессы")
        gp    = QVBoxLayout(grp_p)
        self.proc_tbl = QTableWidget(0, 5)
        self.proc_tbl.setHorizontalHeaderLabels(["PID","Имя","CPU","МБ","Подпись"])
        self.proc_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.proc_tbl.horizontalHeader().resizeSection(0, 55)
        self.proc_tbl.horizontalHeader().resizeSection(2, 55)
        self.proc_tbl.horizontalHeader().resizeSection(3, 55)
        self.proc_tbl.horizontalHeader().resizeSection(4, 75)
        self.proc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.proc_tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.proc_tbl.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)
        gp.addWidget(self.proc_tbl)
        ll.addWidget(grp_p)

        btn_sel_row = QHBoxLayout()
        btn_all = QPushButton("Выбрать все"); btn_all.setObjectName("secondaryBtn")
        btn_all.clicked.connect(self.proc_tbl.selectAll)
        btn_none = QPushButton("Снять"); btn_none.setObjectName("secondaryBtn")
        btn_none.clicked.connect(self.proc_tbl.clearSelection)
        btn_sel_row.addWidget(btn_all); btn_sel_row.addWidget(btn_none)
        ll.addLayout(btn_sel_row)
        splitter.addWidget(left)

        # Right — rules + results
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0,0,0,0); rl.setSpacing(8)

        grp_r = QGroupBox("Правила для сканирования памяти")
        gr    = QVBoxLayout(grp_r)
        self.rule_list = QListWidget()
        self.rule_list.setMaximumHeight(120)
        # Добавляем все правила
        for name in BUILTIN_YARA_RULES:
            item = QListWidgetItem(name)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.rule_list.addItem(item)
        chk_all_btn = QCheckBox("Все правила"); chk_all_btn.setChecked(False)
        chk_all_btn.stateChanged.connect(self._toggle_rules)
        gr.addWidget(chk_all_btn); gr.addWidget(self.rule_list)
        rl.addWidget(grp_r)

        scan_row = QHBoxLayout()
        self.btn_scan = QPushButton("Сканировать память выбранных процессов")
        self.btn_scan.setFixedHeight(36)
        self.btn_scan.clicked.connect(self._start_scan)
        scan_row.addWidget(self.btn_scan)
        self.btn_stop = QPushButton("Стоп"); self.btn_stop.setObjectName("dangerBtn")
        self.btn_stop.setFixedWidth(70); self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_scan)
        scan_row.addWidget(self.btn_stop)
        rl.addLayout(scan_row)

        self.prog = QProgressBar(); self.prog.setRange(0,100); self.prog.setValue(0)
        self.prog.setFixedHeight(5)
        rl.addWidget(self.prog)

        self.scan_status = QLabel("Выбери процессы и нажми сканировать")
        self.scan_status.setStyleSheet("color:#6e7681;font-size:11px;")
        rl.addWidget(self.scan_status)

        # Results
        grp_res = QGroupBox("Результаты сканирования памяти")
        gr2     = QVBoxLayout(grp_res)
        self.res_tbl = QTableWidget(0, 4)
        self.res_tbl.setHorizontalHeaderLabels(["PID","Процесс","Правило","Severity"])
        self.res_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.res_tbl.horizontalHeader().resizeSection(0, 55)
        self.res_tbl.horizontalHeader().resizeSection(1, 140)
        self.res_tbl.horizontalHeader().resizeSection(3, 80)
        self.res_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        gr2.addWidget(self.res_tbl)
        rl.addWidget(grp_res)

        splitter.addWidget(right)
        splitter.setSizes([300, 500])
        lay.addWidget(splitter)

    def _toggle_rules(self, state):
        cs = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for i in range(self.rule_list.count()):
            self.rule_list.item(i).setCheckState(cs)

    def _refresh_procs(self):
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Загрузка...")
        self._w = ProcessListWorker()
        self._w.result.connect(self._on_procs)
        self._w.error.connect(lambda e: (
            self.btn_refresh.setEnabled(True),
            self.btn_refresh.setText("Обновить список процессов"),
            self.scan_status.setText(f"Ошибка: {e}")
        ))
        self._w.finished.connect(lambda: (
            self.btn_refresh.setEnabled(True),
            self.btn_refresh.setText("Обновить список процессов")
        ))
        self._w.start()

    def _on_procs(self, procs):
        self._processes = sorted(procs, key=lambda p: p.get("Name","").lower())
        self._render_procs(self._processes)

    def _render_procs(self, procs):
        self.proc_tbl.setRowCount(0)
        for p in procs:
            row = self.proc_tbl.rowCount()
            self.proc_tbl.insertRow(row)
            signed = p.get("Signed","N/A")
            sign_color = "#3fb950" if signed=="Signed" else "#f85149" if signed=="Unsigned" else "#8b949e"
            vals = [
                str(p.get("PID","")),
                p.get("Name",""),
                str(p.get("CPU","0")),
                str(p.get("MemMB","0")),
                signed,
            ]
            for i, txt in enumerate(vals):
                item = QTableWidgetItem(txt)
                item.setFont(QFont("Consolas", 11))
                item.setData(Qt.ItemDataRole.UserRole, p.get("PID"))
                if i == 4:
                    item.setForeground(QColor(sign_color))
                self.proc_tbl.setItem(row, i, item)
        self.proc_count.setText(f"Процессов: {len(procs)}")

    def _filter_procs(self):
        txt = self.filter_inp.text().lower()
        only_unsigned = self.chk_unsigned.isChecked()
        filtered = [
            p for p in self._processes
            if (not txt or txt in p.get("Name","").lower())
            and (not only_unsigned or p.get("Signed","") != "Signed")
        ]
        self._render_procs(filtered)

    def _get_selected_pids(self):
        rows = set(i.row() for i in self.proc_tbl.selectedItems())
        result = []
        for row in rows:
            pid_item = self.proc_tbl.item(row, 0)
            name_item = self.proc_tbl.item(row, 1)
            if pid_item and name_item:
                try:
                    result.append((int(pid_item.text()), name_item.text()))
                except ValueError:
                    pass
        return result

    def _get_selected_rules(self):
        selected = {}
        for i in range(self.rule_list.count()):
            item = self.rule_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.text()
                if name in BUILTIN_YARA_RULES:
                    selected[name] = BUILTIN_YARA_RULES[name]
        return selected

    def _get_yara_exe(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for p in [
            os.path.join(script_dir, "yara64.exe"),
            r"C:\Tools\yara\yara64.exe",
        ]:
            if os.path.exists(p):
                return p
        return None

    def _start_scan(self):
        pids = self._get_selected_pids()
        if not pids:
            self.scan_status.setText("Выбери процессы из списка (используй Shift/Ctrl для множественного выбора)")
            return
        rules = self._get_selected_rules()
        if not rules:
            self.scan_status.setText("Выбери хотя бы одно правило")
            return
        yara_exe = self._get_yara_exe()
        if not yara_exe:
            self.scan_status.setText("yara64.exe не найден — положи его рядом с main.py")
            return

        self.res_tbl.setRowCount(0)
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.prog.setValue(0)
        self.scan_status.setText(f"Сканируется {len(pids)} процессов...")

        self._scan_worker = MemScanWorker(pids, rules, yara_exe)
        self._scan_worker.progress.connect(self._on_progress)
        self._scan_worker.hit.connect(self._on_hit)
        self._scan_worker.done.connect(self._on_done)
        self._scan_worker.error.connect(self._on_error)
        self._scan_worker.start()

    def _stop_scan(self):
        if self._scan_worker:
            self._scan_worker.stop()
        self.btn_stop.setEnabled(False)
        self.scan_status.setText("Остановлено пользователем")

    def _on_progress(self, current, total, name):
        self.prog.setValue(int(current / total * 100))
        self.scan_status.setText(f"Сканирую [{current}/{total}]: {name}")

    def _on_hit(self, hit):
        sev_map = {}
        for rule_text in BUILTIN_YARA_RULES.values():
            m  = re.search(r'severity\s*=\s*"(\w+)"', rule_text)
            rn = re.search(r'rule\s+(\w+)', rule_text)
            if m and rn:
                sev_map[rn.group(1)] = m.group(1)

        rule = hit.get("rule","?")
        sev  = sev_map.get(rule, "high")
        col  = {"critical":"#f85149","high":"#d29922",
                "medium":"#58a6ff","low":"#3fb950"}.get(sev,"#d29922")

        row = self.res_tbl.rowCount()
        self.res_tbl.insertRow(row)
        for i, txt in enumerate([str(hit["pid"]), hit["name"], rule, sev.upper()]):
            item = QTableWidgetItem(txt)
            item.setFont(QFont("Consolas", 11))
            if i in (2,3):
                item.setForeground(QColor(col))
            self.res_tbl.setItem(row, i, item)

        DashboardTab.log_event(
            "MEMSCAN", f"{hit['name']} (PID {hit['pid']}) — {rule}",
            level="critical" if sev=="critical" else "high",
            severity=sev.capitalize(), scan=True, target=hit["name"]
        )

    def _on_done(self, total_hits):
        self.prog.setValue(100)
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if total_hits == 0:
            self.scan_status.setText("Сканирование завершено — совпадений не найдено")
        else:
            self.scan_status.setText(f"Завершено — найдено совпадений в памяти: {total_hits}")

    def _on_error(self, msg):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.scan_status.setText(f"Ошибка: {msg}")

import os
import re
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTextEdit, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFrame, QListWidget, QListWidgetItem,
    QCheckBox, QSplitter
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from config import Config, get_results_dir
from constants import BUILTIN_YARA_RULES
from core.yara_engine import YARA_PYTHON_AVAILABLE
from core.i18n import t
from core.lang_signal import lang_signal
from workers.yara_worker import YARAWorker
from ui.dashboard_tab import DashboardTab


class YARATab(QWidget):
    def __init__(self):
        super().__init__()
        self._custom_rules = {}
        self._build()
        lang_signal.changed.connect(self.retranslate)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: rule selector
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        self.grp_rules = QGroupBox(t("yara_builtin_rules"))
        gr = QVBoxLayout(self.grp_rules)
        self.rule_list = QListWidget()
        self.rule_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        for name in BUILTIN_YARA_RULES:
            item = QListWidgetItem(name)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.rule_list.addItem(item)
        self.chk_all = QCheckBox(t("yara_select_all"))
        self.chk_all.clicked.connect(self._toggle_all)
        gr.addWidget(self.chk_all)
        gr.addWidget(self.rule_list)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton(t("yara_load_yar"))
        self.btn_add.setObjectName("secondaryBtn")
        self.btn_add.clicked.connect(self._load_yar)
        btn_row.addWidget(self.btn_add)
        gr.addLayout(btn_row)
        ll.addWidget(self.grp_rules)

        splitter.addWidget(left)

        # Right: scan target + results
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        self.grp_target = QGroupBox(t("yara_scan_target"))
        gt = QVBoxLayout(self.grp_target)

        tr = QHBoxLayout()
        self.target_inp = QLineEdit()
        self.target_inp.setPlaceholderText(t("yara_placeholder"))
        tr.addWidget(self.target_inp)
        self.btn_file = QPushButton(t("yara_file"))
        self.btn_file.setObjectName("secondaryBtn")
        self.btn_file.setMinimumWidth(80)
        self.btn_file.clicked.connect(self._browse_file)
        tr.addWidget(self.btn_file)
        self.btn_dir = QPushButton(t("yara_folder"))
        self.btn_dir.setObjectName("secondaryBtn")
        self.btn_dir.setMinimumWidth(80)
        self.btn_dir.clicked.connect(self._browse_dir)
        tr.addWidget(self.btn_dir)
        gt.addLayout(tr)
        rl.addWidget(self.grp_target)

        scan_row = QHBoxLayout()
        self.btn_scan = QPushButton(t("yara_scan_btn"))
        self.btn_scan.setFixedHeight(36)
        self.btn_scan.clicked.connect(self._scan)
        scan_row.addWidget(self.btn_scan)
        self.btn_edit = QPushButton(t("yara_rule_editor_btn"))
        self.btn_edit.setObjectName("accentBtn")
        self.btn_edit.setMinimumWidth(150)
        self.btn_edit.clicked.connect(self._open_editor)
        scan_row.addWidget(self.btn_edit)
        rl.addLayout(scan_row)

        self.prog = QProgressBar()
        self.prog.setRange(0, 0)
        self.prog.setVisible(False)
        self.prog.setFixedHeight(5)
        rl.addWidget(self.prog)

        self.grp_results = QGroupBox(t("yara_results"))
        gr2 = QVBoxLayout(self.grp_results)
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels([t("yara_rule_lbl"), t("yara_severity_lbl"), t("yara_file_lbl")])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().resizeSection(0, 180)
        self.tbl.horizontalHeader().resizeSection(1, 80)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        gr2.addWidget(self.tbl)

        self.scan_status = QLabel(t("yara_ready"))
        self.scan_status.setStyleSheet("color:#8b949e;font-size:11px;")
        gr2.addWidget(self.scan_status)
        rl.addWidget(self.grp_results)

        splitter.addWidget(right)
        splitter.setSizes([220, 500])
        lay.addWidget(splitter)

        # Editor widget (hidden by default)
        self.editor_frame = QFrame()
        self.editor_frame.setVisible(False)
        ef = QVBoxLayout(self.editor_frame)
        self.lbl_editor = QLabel(t("yara_editor_title"))
        ef.addWidget(self.lbl_editor)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText(
            'rule MyRule {\n    meta:\n        description = "..."\n    strings:\n        $s1 = "evil" ascii\n    condition:\n        $s1\n}')
        ef.addWidget(self.editor)
        er = QHBoxLayout()
        self.btn_save_rule = QPushButton(t("yara_save_rule"))
        self.btn_save_rule.clicked.connect(self._save_rule)
        self.btn_close_editor = QPushButton(t("yara_close"))
        self.btn_close_editor.setObjectName("secondaryBtn")
        self.btn_close_editor.clicked.connect(lambda: self.editor_frame.setVisible(False))
        er.addWidget(self.btn_save_rule)
        er.addWidget(self.btn_close_editor)
        ef.addLayout(er)
        lay.addWidget(self.editor_frame)

    def retranslate(self, _lang: str = ""):
        self.grp_rules.setTitle(t("yara_builtin_rules"))
        self.chk_all.setText(t("yara_select_all"))
        self.btn_add.setText(t("yara_load_yar"))
        self.grp_target.setTitle(t("yara_scan_target"))
        self.target_inp.setPlaceholderText(t("yara_placeholder"))
        self.btn_file.setText(t("yara_file"))
        self.btn_dir.setText(t("yara_folder"))
        self.btn_scan.setText(t("yara_scan_btn"))
        self.btn_edit.setText(t("yara_rule_editor_btn"))
        self.grp_results.setTitle(t("yara_results"))
        self.tbl.setHorizontalHeaderLabels([t("yara_rule_lbl"), t("yara_severity_lbl"), t("yara_file_lbl")])
        self.lbl_editor.setText(t("yara_editor_title"))
        self.btn_save_rule.setText(t("yara_save_rule"))
        self.btn_close_editor.setText(t("yara_close"))
        if self.scan_status.text() in (t("yara_ready"), "Ready to scan", "Готов к сканированию", "Сканерлеуге дайын"):
            self.scan_status.setText(t("yara_ready"))

    def _toggle_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.rule_list.count()):
            self.rule_list.item(i).setCheckState(state)

    def _load_yar(self):
        paths, _ = QFileDialog.getOpenFileNames(self, t("yara_load_yar"), "", "YARA (*.yar *.yara);;All (*)")
        for p in paths:
            name = Path(p).stem
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self._custom_rules[name] = content
            item = QListWidgetItem(f"📄 {name} [custom]")
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, content)
            self.rule_list.addItem(item)

    def _browse_file(self):
        p, _ = QFileDialog.getOpenFileName(self, t("yara_file"))
        if p:
            self.target_inp.setText(p)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, t("yara_folder"))
        if d:
            self.target_inp.setText(d)

    def _get_selected_rules(self):
        selected = {}
        for i in range(self.rule_list.count()):
            item = self.rule_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                raw_name    = item.text().replace("📄 ", "").split(" [custom]")[0].strip()
                custom_data = item.data(Qt.ItemDataRole.UserRole)
                if custom_data:
                    selected[raw_name] = custom_data
                elif raw_name in BUILTIN_YARA_RULES:
                    selected[raw_name] = BUILTIN_YARA_RULES[raw_name]
        return selected

    def _scan(self):
        target = self.target_inp.text().strip()
        if not target:
            self.scan_status.setText(t("yara_no_path"))
            return
        if not os.path.exists(target):
            self.scan_status.setText(t("yara_bad_path"))
            return
        rules = self._get_selected_rules()
        if not rules:
            self.scan_status.setText(t("yara_no_rules"))
            return

        self.btn_scan.setEnabled(False)
        self.prog.setVisible(True)
        self.tbl.setRowCount(0)
        self.scan_status.setText(t("yara_scanning"))

        self._w = YARAWorker(rules, target, str(get_results_dir()))
        self.scan_status.setText(
            f"{t('yara_scanning')} rules: {len(rules)}, "
            f"engine: {'yara-python' if YARA_PYTHON_AVAILABLE else 'yara64.exe'}"
        )
        self._w.done.connect(self._on_scan_done)
        self._w.error.connect(self._on_scan_error)
        self._w.finished.connect(lambda: (self.btn_scan.setEnabled(True), self.prog.setVisible(False)))
        self._w.start()

    def _on_scan_done(self, matches):
        severity_map = {}
        for rule_text in BUILTIN_YARA_RULES.values():
            m  = re.search(r'severity\s*=\s*"(\w+)"', rule_text)
            rn = re.search(r'rule\s+(\w+)', rule_text)
            if m and rn:
                severity_map[rn.group(1)] = m.group(1)

        colors = {
            "critical": "#f85149", "high": "#d29922",
            "medium":   "#58a6ff", "low":  "#3fb950",
            "info":     "#8b949e", "ERROR": "#f85149",
            "COMPILE_ERR": "#d29922", "WARN": "#d29922",
            "TIMEOUT":  "#8b949e", "INFO":  "#8b949e",
            "DEBUG":    "#484f58",
        }

        self.tbl.setRowCount(len(matches))
        real_hits = []
        for i, match in enumerate(matches):
            rule  = match.get("rule", "?")
            fpath = match.get("file", "?")
            sev   = severity_map.get(rule, rule.lower() if rule in colors else "info")
            col   = colors.get(sev, colors.get(rule, "#8b949e"))

            ri = QTableWidgetItem(rule)
            si = QTableWidgetItem(sev.upper() if sev not in colors else sev)
            fi = QTableWidgetItem(fpath)
            si.setForeground(QColor(col))
            ri.setForeground(QColor(col))
            for itm in (ri, si, fi):
                itm.setFont(QFont("Consolas", 11))
            self.tbl.setItem(i, 0, ri)
            self.tbl.setItem(i, 1, si)
            self.tbl.setItem(i, 2, fi)

            if rule not in ("ERROR", "TIMEOUT", "INFO", "COMPILE_ERR", "WARN", "DEBUG"):
                real_hits.append(match)

        if not matches:
            self.scan_status.setText(t("yara_clean"))
        elif real_hits:
            self.scan_status.setText(t("yara_found", n=len(real_hits)))
            DashboardTab.stats["yara_scans"] += 1
            DashboardTab.stats["yara_hits"]  += len(real_hits)
            for h in real_hits:
                sev_val = severity_map.get(h.get("rule", ""), "Medium")
                lvl = {"critical": "critical", "high": "high", "medium": "info"}.get(sev_val.lower(), "info")
                DashboardTab.log_event(
                    "YARA", f"{h.get('rule','?')} → {Path(h.get('file','?')).name}",
                    level=lvl, severity=sev_val.capitalize(),
                    scan=True, target=h.get("file", "?"),
                )
        else:
            first = matches[0]
            if first["rule"] == "INFO":
                self.scan_status.setText(first["file"].split("\n")[0])
            elif first["rule"] == "COMPILE_ERR":
                self.scan_status.setText(f"Compile error: {first['file'][:80]}")
            else:
                self.scan_status.setText(t("yara_clean"))

    def _on_scan_error(self, msg):
        self.scan_status.setText(f"✘ {msg}")

    def _open_editor(self):
        self.editor_frame.setVisible(not self.editor_frame.isVisible())

    def _save_rule(self):
        text = self.editor.toPlainText().strip()
        if not text:
            return
        m    = re.search(r'rule\s+(\w+)', text)
        name = m.group(1) if m else f"CustomRule_{len(self._custom_rules)+1}"
        self._custom_rules[name] = text
        item = QListWidgetItem(f"📄 {name} [custom]")
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, text)
        self.rule_list.addItem(item)
        self.editor_frame.setVisible(False)
        self.editor.clear()

import os
import hashlib
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTextEdit, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from config import Config, VT_URL
from workers.vt_worker import VTWorker, BulkHashWorker
from ui.dashboard_tab import DashboardTab


class HashTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10); lay.setContentsMargins(16,16,16,16)

        grp = QGroupBox("Hash Lookup — VirusTotal API v3")
        gl  = QVBoxLayout(grp); gl.setSpacing(8)

        row = QHBoxLayout()
        self.inp = QLineEdit(); self.inp.setPlaceholderText("MD5 / SHA1 / SHA256 ...")
        self.inp.returnPressed.connect(self._lookup)
        row.addWidget(self.inp)
        self.btn_lookup = QPushButton("Поиск")
        self.btn_lookup.setFixedWidth(110); self.btn_lookup.clicked.connect(self._lookup)
        row.addWidget(self.btn_lookup); gl.addLayout(row)

        row2 = QHBoxLayout()
        lbl = QLabel("Или вычислить SHA256 из файла / папки:"); lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        row2.addWidget(lbl); row2.addStretch()
        btn_f = QPushButton("Обзор"); btn_f.setObjectName("secondaryBtn")
        btn_f.setFixedWidth(80); btn_f.clicked.connect(self._browse)
        row2.addWidget(btn_f)
        btn_d = QPushButton("Скан папки"); btn_d.setObjectName("accentBtn")
        btn_d.setFixedWidth(120); btn_d.clicked.connect(self._scan_folder)
        row2.addWidget(btn_d)
        gl.addLayout(row2)
        lay.addWidget(grp)

        self.status = QLabel("Ready"); self.status.setStyleSheet("color:#8b949e;font-size:11px;")
        lay.addWidget(self.status)
        self.prog = QProgressBar(); self.prog.setRange(0,0); self.prog.setVisible(False)
        self.prog.setFixedHeight(5); lay.addWidget(self.prog)

        # Verdict card
        vf = QFrame(); vf.setStyleSheet("QFrame{background:#161b22;border:1px solid #30363d;border-radius:8px;}")
        vfl = QVBoxLayout(vf); vfl.setContentsMargins(16,10,16,10)
        QLabel("ВЕРДИКТ", vf).setStyleSheet("color:#8b949e;font-size:9px;font-weight:bold;letter-spacing:2px;")
        vfl.addWidget(vf.findChild(QLabel))
        self.verdict = QLabel("—"); self.verdict.setStyleSheet("font-size:26px;font-weight:bold;color:#58a6ff;")
        vfl.addWidget(self.verdict)
        self.vdetail = QLabel(""); self.vdetail.setStyleSheet("color:#8b949e;font-size:11px;")
        vfl.addWidget(self.vdetail)
        lay.addWidget(vf)

        self.out = QTextEdit(); self.out.setReadOnly(True)
        self.out.setPlaceholderText("Результаты появятся здесь...")
        self.out.setMaximumHeight(180)
        lay.addWidget(self.out)

        # Bulk results table (для массового сканирования папки)
        self.bulk_grp = QGroupBox("Массовое сканирование")
        bgl = QVBoxLayout(self.bulk_grp)
        self.bulk_tbl = QTableWidget(0, 4)
        self.bulk_tbl.setHorizontalHeaderLabels(["Файл", "SHA256", "Вердикт", "Детектов"])
        self.bulk_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bulk_tbl.horizontalHeader().resizeSection(1, 200)
        self.bulk_tbl.horizontalHeader().resizeSection(2, 130)
        self.bulk_tbl.horizontalHeader().resizeSection(3, 80)
        self.bulk_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        bgl.addWidget(self.bulk_tbl)
        self.bulk_grp.setVisible(False)
        lay.addWidget(self.bulk_grp)

    def _scan_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Выбери папку для массового скана")
        if not d:
            return
        # Собираем список файлов
        files = []
        for root, _, fnames in os.walk(d):
            for fn in fnames:
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > 0:
                        files.append(fp)
                except OSError:
                    pass
        if not files:
            self.status.setText("В папке нет файлов")
            return

        # Лимит для бесплатного VT API — 4 запроса в минуту
        if len(files) > 50:
            r = QMessageBox.question(
                self, "Много файлов",
                f"Найдено {len(files)} файлов. VirusTotal бесплатный API имеет лимит 4 запроса/мин.\n"
                f"Скан займёт примерно {len(files)//4} минут.\n\nПродолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        self.bulk_tbl.setRowCount(0)
        self.bulk_grp.setVisible(True)
        self.btn_lookup.setEnabled(False)
        self.prog.setVisible(True)
        self.status.setText(f"Сканирую {len(files)} файлов...")

        self._bulk_w = BulkHashWorker(files)
        self._bulk_w.progress.connect(self._on_bulk_progress)
        self._bulk_w.file_done.connect(self._on_bulk_file)
        self._bulk_w.all_done.connect(self._on_bulk_done)
        self._bulk_w.start()

    def _on_bulk_progress(self, current, total, name):
        self.status.setText(f"[{current}/{total}] Проверяю: {name}")

    def _on_bulk_file(self, info):
        """info: {file, sha256, status, mal, total, name}"""
        row = self.bulk_tbl.rowCount()
        self.bulk_tbl.insertRow(row)
        status = info["status"]
        mal    = info.get("mal", 0)
        total  = info.get("total", 0)

        if status == "MALICIOUS":
            col = "#f85149"
        elif status == "SUSPICIOUS":
            col = "#d29922"
        elif status == "CLEAN":
            col = "#3fb950"
        elif status == "NOT_FOUND":
            col = "#58a6ff"
        else:
            col = "#8b949e"

        items = [
            os.path.basename(info["file"]),
            info["sha256"][:32] + "...",
            status,
            f"{mal}/{total}" if total else "—"
        ]
        for i, txt in enumerate(items):
            item = QTableWidgetItem(txt)
            item.setFont(QFont("Consolas", 11))
            if i == 2:
                item.setForeground(QColor(col))
            item.setToolTip(info["file"])
            self.bulk_tbl.setItem(row, i, item)

        # Логируем в Dashboard
        DashboardTab.stats["hash_lookups"] += 1
        if status == "MALICIOUS":
            DashboardTab.stats["malicious"] += 1
            DashboardTab.log_event("HASH",
                f"{os.path.basename(info['file'])} — MALICIOUS ({mal}/{total})",
                level="critical", severity="Critical", scan=True,
                target=os.path.basename(info["file"]))
        elif status == "CLEAN":
            DashboardTab.stats["clean"] += 1

    def _on_bulk_done(self, total_scanned):
        self.btn_lookup.setEnabled(True)
        self.prog.setVisible(False)
        self.status.setText(f"Массовый скан завершён: проверено {total_scanned} файлов")

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Select File")
        if not p: return
        sha256 = hashlib.sha256()
        with open(p,"rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        h = sha256.hexdigest()
        self.inp.setText(h)
        self.status.setText(f"SHA256: {h}")

    def _lookup(self):
        h = self.inp.text().strip()
        if not h: return
        if len(h) not in (32,40,64):
            self.status.setText("⚠ Некорректная длина хэша"); return
        self.btn_lookup.setEnabled(False); self.prog.setVisible(True)
        self.status.setText(f"Запрос к VirusTotal: {h[:16]}...")
        self.out.clear(); self.verdict.setText("—")
        self._w = VTWorker(h)
        self._w.result.connect(self._on_result)
        self._w.error.connect(self._on_error)
        self._w.finished.connect(lambda: (self.btn_lookup.setEnabled(True), self.prog.setVisible(False)))
        self._w.start()

    def _on_result(self, data):
        a = data.get("data",{}).get("attributes",{})
        stats = a.get("last_analysis_stats",{})
        mal   = stats.get("malicious",0)
        sus   = stats.get("suspicious",0)
        total = sum(stats.values())
        name  = a.get("meaningful_name","Unknown")
        ftype = a.get("type_description","")
        size  = a.get("size",0)

        if mal >= 5:
            self.verdict.setText(f"🔴  MALICIOUS  ({mal}/{total})")
            self.verdict.setStyleSheet("font-size:22px;font-weight:bold;color:#f85149;")
        elif mal > 0 or sus > 0:
            self.verdict.setText(f"🟡  SUSPICIOUS  ({mal+sus}/{total})")
            self.verdict.setStyleSheet("font-size:22px;font-weight:bold;color:#d29922;")
        else:
            self.verdict.setText(f"🟢  CLEAN  (0/{total})")
            self.verdict.setStyleSheet("font-size:22px;font-weight:bold;color:#3fb950;")
        self.vdetail.setText(f"{name}  ·  {ftype}  ·  {size:,} bytes")

        lines = ["="*55,"  VIRUSTOTAL REPORT","="*55,
                 f"  Name  : {name}",f"  Type  : {ftype}",f"  Size  : {size:,} bytes",
                 f"  MD5   : {a.get('md5','N/A')}",f"  SHA1  : {a.get('sha1','N/A')}",
                 f"  SHA256: {a.get('sha256','N/A')}","",
                 f"  Malicious : {mal}",f"  Suspicious: {sus}",
                 f"  Harmless  : {stats.get('harmless',0)}",
                 f"  Total     : {total}",""]
        detected = {k:v for k,v in a.get("last_analysis_results",{}).items()
                    if v.get("category") in ("malicious","suspicious")}
        if detected:
            lines.append(f"  DETECTIONS ({len(detected)}):")
            for av,info in list(detected.items())[:25]:
                lines.append(f"    [{info['category'][:4].upper()}] {av}: {info.get('result','?')}")
        lines.append("="*55)
        self.out.setText("\n".join(lines))
        self.status.setText(f"✔ {mal} malicious / {total} engines")
        # Используем sha256 из ответа VT (h не определена в этой области видимости)
        h_short = a.get('sha256', a.get('md5', 'unknown'))[:16]
        DashboardTab.stats["hash_lookups"] += 1
        if mal >= 5:
            DashboardTab.stats["malicious"] += 1
            DashboardTab.log_event("HASH", f"{h_short}... — MALICIOUS ({mal}/{total})",
                level="critical", severity="Critical", scan=True, target=h_short+"...")
        elif mal > 0:
            DashboardTab.log_event("HASH", f"{h_short}... — suspicious ({mal}/{total})",
                level="high", severity="High", scan=True, target=h_short+"...")
        else:
            DashboardTab.stats["clean"] += 1
            DashboardTab.log_event("HASH", f"{h_short}... — clean",
                level="ok", severity="Low", scan=True, target=h_short+"...")

    def _on_error(self, msg):
        self.verdict.setText("⚠  ERROR")
        self.verdict.setStyleSheet("font-size:22px;font-weight:bold;color:#8b949e;")
        self.out.setText(f"Ошибка: {msg}")
        self.status.setText(f"✘ {msg}")

import os
import json
import hashlib
import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from config import Config, get_quarantine_dir
from ui.dashboard_tab import DashboardTab
from core.i18n import t


class QuarantineTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()
        self._load()

    def _build(self):
        lay = QVBoxLayout(self); lay.setSpacing(10); lay.setContentsMargins(16,16,16,16)

        # Config
        self._grp_dir = QGroupBox(t("quar_dir_grp"))
        gl = QHBoxLayout(self._grp_dir)
        self.dir_inp = QLineEdit(str(get_quarantine_dir()))
        gl.addWidget(self.dir_inp)
        self._btn_browse = QPushButton(t("quar_browse_btn"))
        self._btn_browse.setObjectName("secondaryBtn"); self._btn_browse.setFixedWidth(80)
        self._btn_browse.clicked.connect(self._browse_dir); gl.addWidget(self._btn_browse)
        lay.addWidget(self._grp_dir)

        # Action buttons
        btn_row = QHBoxLayout()
        self.btn_add = QPushButton(t("quar_add_btn"))
        self.btn_add.setFixedHeight(36); self.btn_add.clicked.connect(self._quarantine_file)
        btn_row.addWidget(self.btn_add)

        self.btn_restore = QPushButton(t("quar_restore_btn"))
        self.btn_restore.setObjectName("secondaryBtn"); self.btn_restore.setFixedWidth(130)
        self.btn_restore.clicked.connect(self._restore); btn_row.addWidget(self.btn_restore)

        self.btn_delete = QPushButton(t("quar_delete_btn"))
        self.btn_delete.setObjectName("dangerBtn"); self.btn_delete.setFixedWidth(140)
        self.btn_delete.clicked.connect(self._delete_perm); btn_row.addWidget(self.btn_delete)

        self.btn_open = QPushButton(t("quar_open_btn"))
        self.btn_open.setObjectName("secondaryBtn"); self.btn_open.setFixedWidth(120)
        self.btn_open.clicked.connect(self._open_dir); btn_row.addWidget(self.btn_open)
        lay.addLayout(btn_row)

        # Table
        self._grp_files = QGroupBox(t("quar_files_grp"))
        gl2 = QVBoxLayout(self._grp_files)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels([
            t("quar_tbl_name"), t("quar_tbl_size"),
            t("quar_tbl_date"), t("quar_tbl_sha"), t("quar_tbl_status"),
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().resizeSection(1, 80)
        self.tbl.horizontalHeader().resizeSection(2, 140)
        self.tbl.horizontalHeader().resizeSection(3, 200)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        gl2.addWidget(self.tbl)
        lay.addWidget(self._grp_files)

        self.status = QLabel(t("quar_empty"))
        self.status.setStyleSheet("color:#6e7681;font-size:11px;")
        lay.addWidget(self.status)

    def retranslate(self, _lang: str = ""):
        self._grp_dir.setTitle(t("quar_dir_grp"))
        self._btn_browse.setText(t("quar_browse_btn"))
        self.btn_add.setText(t("quar_add_btn"))
        self.btn_restore.setText(t("quar_restore_btn"))
        self.btn_delete.setText(t("quar_delete_btn"))
        self.btn_open.setText(t("quar_open_btn"))
        self._grp_files.setTitle(t("quar_files_grp"))
        self.tbl.setHorizontalHeaderLabels([
            t("quar_tbl_name"), t("quar_tbl_size"),
            t("quar_tbl_date"), t("quar_tbl_sha"), t("quar_tbl_status"),
        ])
        self._load()

    def _get_qdir(self):
        d = Path(self.dir_inp.text())
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, t("quar_browse_dialog"))
        if d: self.dir_inp.setText(d)

    def _quarantine_file(self, filepath=None):
        if not filepath:
            filepath, _ = QFileDialog.getOpenFileName(self, t("quar_add_dialog"))
        if not filepath or not os.path.exists(filepath):
            return
        try:
            qdir = self._get_qdir()
            src  = Path(filepath)
            sha256 = hashlib.sha256()
            with open(src, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            h = sha256.hexdigest()

            dst = qdir / f"{h[:16]}.quar"
            meta_path = qdir / f"{h[:16]}.meta"

            with open(src, "rb") as f:
                data = f.read()
            enc = bytes(b ^ 0xAA for b in data)
            with open(dst, "wb") as f:
                f.write(enc)

            meta = {
                "original_name": src.name,
                "original_path": str(src),
                "sha256": h,
                "size": len(data),
                "quarantined_at": datetime.datetime.now().isoformat(),
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            src.unlink()
            self._load()
            self.status.setText(t("quar_isolated", name=src.name))
            DashboardTab.log_event("QUARANTINE", f"Файл изолирован: {src.name}",
                                   level="high", scan=True, target=src.name)
        except Exception as e:
            self.status.setText(t("quar_error", msg=str(e)))

    def _load(self):
        self.tbl.setRowCount(0)
        qdir = self._get_qdir()
        for meta_path in sorted(qdir.glob("*.meta")):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                row = self.tbl.rowCount()
                self.tbl.insertRow(row)
                size_kb = meta.get("size", 0) // 1024
                dt = meta.get("quarantined_at","")[:19].replace("T"," ")
                items = [
                    meta.get("original_name","?"),
                    f"{size_kb} KB",
                    dt,
                    meta.get("sha256","")[:32] + "...",
                    t("quar_cell_isolated"),
                ]
                for i, txt in enumerate(items):
                    item = QTableWidgetItem(txt)
                    item.setFont(QFont("Consolas", 11))
                    if i == 4:
                        item.setForeground(QColor("#d29922"))
                    item.setData(Qt.ItemDataRole.UserRole, str(meta_path))
                    self.tbl.setItem(row, i, item)
            except Exception:
                pass
        n = self.tbl.rowCount()
        self.status.setText(t("quar_count", n=n) if n else t("quar_empty"))

    def _restore(self):
        row = self.tbl.currentRow()
        if row < 0: return
        meta_path = Path(self.tbl.item(row, 0).data(Qt.ItemDataRole.UserRole))
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            quar_path = meta_path.with_suffix(".quar")
            orig_path = Path(meta["original_path"])
            with open(quar_path, "rb") as f:
                enc = f.read()
            data = bytes(b ^ 0xAA for b in enc)
            with open(orig_path, "wb") as f:
                f.write(data)
            quar_path.unlink(); meta_path.unlink()
            self._load()
            self.status.setText(t("quar_restored", name=meta["original_name"]))
        except Exception as e:
            self.status.setText(t("quar_restore_error", e=str(e)))

    def _delete_perm(self):
        row = self.tbl.currentRow()
        if row < 0: return
        meta_path = Path(self.tbl.item(row, 0).data(Qt.ItemDataRole.UserRole))
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            name = meta.get("original_name","?")
            msg = QMessageBox.question(self, t("quar_delete_title"),
                t("quar_delete_msg", name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if msg == QMessageBox.StandardButton.Yes:
                meta_path.with_suffix(".quar").unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                self._load()
                self.status.setText(t("quar_deleted", name=name))
        except Exception as e:
            self.status.setText(t("quar_error", msg=str(e)))

    def _open_dir(self):
        d = self.dir_inp.text()
        if os.path.exists(d):
            os.startfile(d)

    def quarantine_from_path(self, filepath):
        self._quarantine_file(filepath)

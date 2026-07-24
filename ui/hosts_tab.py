import csv
import re
import socket
from datetime import datetime
from config import Config

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QListWidget,
    QListWidgetItem, QCheckBox, QSplitter, QLineEdit, QDialog, QFormLayout,
    QDialogButtonBox, QMessageBox, QSpinBox, QTabWidget, QTextEdit,
    QFileDialog, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from core.hosts_config import load_hosts, add_host, remove_host, update_host
from workers.host_worker import (
    PingWorker, RemoteScanWorker, DeployWorker,
    RemoteProcessListWorker, RemoteMemScanWorker,
    NetworkIsolationWorker, RemoteHashVTWorker,
    RemoteInfoWorker,
)
from constants import BUILTIN_YARA_RULES
from core.i18n import t
from core.lang_signal import lang_signal
from ui.dashboard_tab import DashboardTab


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


class _AddHostDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("hosts_add_dialog_title"))
        self.setMinimumWidth(360)
        layout = QFormLayout(self)
        self._name  = QLineEdit(); self._name.setPlaceholderText("WS-FINANCE01")
        self._ip    = QLineEdit(); self._ip.setPlaceholderText("192.168.1.10")
        self._port  = QSpinBox();  self._port.setRange(1, 65535); self._port.setValue(5555)
        self._token = QLineEdit(); self._token.setPlaceholderText("вставьте токен из token.txt")
        layout.addRow(t("hosts_add_name"),  self._name)
        layout.addRow(t("hosts_add_ip"),    self._ip)
        layout.addRow(t("hosts_add_port"),  self._port)
        layout.addRow(t("hosts_add_token"), self._token)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def data(self):
        return {"name": self._name.text().strip(), "ip": self._ip.text().strip(),
                "port": self._port.value(), "token": self._token.text().strip()}


class _DeployDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("hosts_deploy_dialog_title"))
        self.setMinimumWidth(360)
        layout = QFormLayout(self)
        self._ip   = QLineEdit(); self._ip.setPlaceholderText("192.168.1.10")
        self._user = QLineEdit(); self._user.setPlaceholderText("DOMAIN\\admin")
        self._pwd  = QLineEdit(); self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow(t("hosts_deploy_ip"),   self._ip)
        layout.addRow(t("hosts_deploy_user"), self._user)
        layout.addRow(t("hosts_deploy_pwd"),  self._pwd)
        note = QLabel(t("hosts_deploy_note"))
        note.setStyleSheet("color:#8b949e;font-size:11px;"); note.setWordWrap(True)
        layout.addRow(note)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def data(self):
        return {"ip": self._ip.text().strip(),
                "username": self._user.text().strip(),
                "password": self._pwd.text()}


# ─────────────────────────────────────────────────────────────────────────────

class HostsTab(QWidget):
    _TAB_STATUS   = 0
    _TAB_FILE     = 1
    _TAB_MEMORY   = 2
    _TAB_RESULTS  = 3
    _TAB_ISOLATE  = 4

    def __init__(self, on_host_changed=None, on_hosts_list_changed=None):
        super().__init__()
        self._on_host_changed       = on_host_changed
        self._on_hosts_list_changed = on_hosts_list_changed
        self._selected_id: str | None = None

        self._ping_worker:   PingWorker | None = None
        self._scan_worker:   RemoteScanWorker | None = None
        self._deploy_worker: DeployWorker | None = None
        self._proc_worker:   RemoteProcessListWorker | None = None
        self._mem_worker:    RemoteMemScanWorker | None = None
        self._iso_worker:    NetworkIsolationWorker | None = None
        self._vt_worker:     RemoteHashVTWorker | None = None
        self._info_worker:   RemoteInfoWorker | None = None
        self._hash_row_map: dict = {}   # file_path -> (row_index, sha256)

        self._mem_stop_requested: bool = False
        self._shared_custom_rules: dict = {}
        self._file_custom_rules = self._shared_custom_rules
        self._mem_custom_rules  = self._shared_custom_rules
        self._remote_procs: list = []
        self._vt_results: list = []

        self._build()
        self._showing_hint = True
        self._reload_hosts()
        self._start_ping_timer()
        lang_signal.changed.connect(self.retranslate)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self); lay.setSpacing(8); lay.setContentsMargins(12, 12, 12, 12)

        outer = QSplitter(Qt.Orientation.Horizontal)

        # ── LEFT: host list ───────────────────────────────────────────────
        left = QWidget(); ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(6)

        self._lbl_count = QLabel(t("hosts_count", n=0))
        self._lbl_count.setStyleSheet(
            "color:#6e7681;font-size:11px;font-weight:bold;letter-spacing:0.5px;")
        ll.addWidget(self._lbl_count)

        self._host_list = QListWidget()
        self._host_list.setMinimumWidth(220)
        self._host_list.currentRowChanged.connect(self._on_host_select)
        ll.addWidget(self._host_list, 1)

        row_btns = QHBoxLayout()
        self._btn_add = QPushButton(t("hosts_add_btn"))
        self._btn_add.setObjectName("secondaryBtn")
        self._btn_add.clicked.connect(self._add_host)
        self._btn_remove = QPushButton(t("hosts_remove_btn"))
        self._btn_remove.setObjectName("dangerBtn"); self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._remove_host)
        row_btns.addWidget(self._btn_add); row_btns.addWidget(self._btn_remove)
        ll.addLayout(row_btns)

        self._btn_import_csv = QPushButton("📥  Импорт CSV")
        self._btn_import_csv.setObjectName("secondaryBtn")
        self._btn_import_csv.setToolTip(
            "Импортировать хосты из CSV-файла.\n"
            "Формат: name,ip,port,token (заголовок обязателен)")
        self._btn_import_csv.clicked.connect(self._import_hosts_csv)
        ll.addWidget(self._btn_import_csv)
        outer.addWidget(left)

        # ── RIGHT: content ─────────────────────────────────────────────────
        right = QWidget(); rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)

        self._info_label = QLabel(t("hosts_select_hint"))
        self._info_label.setStyleSheet(
            "color:#8b949e;font-size:12px;padding:6px 10px;"
            "background:#161b22;border:1px solid #21262d;border-radius:6px;")
        self._info_label.setWordWrap(True)
        rl.addWidget(self._info_label)

        self._sub_tabs = QTabWidget()
        self._sub_tabs.setEnabled(False)
        self._sub_tabs.setUsesScrollButtons(True)
        self._sub_tabs.setStyleSheet(
            "QTabBar::tab{padding:8px 16px;font-size:12px;min-height:22px;}"
            "QTabBar::tab:selected{font-weight:bold;}")
        self._sub_tabs.addTab(self._build_status_tab(),  t("hosts_sub_status"))
        self._sub_tabs.addTab(self._build_file_tab(),    t("hosts_sub_file"))
        self._sub_tabs.addTab(self._build_memory_tab(),  t("hosts_sub_memory"))
        self._sub_tabs.addTab(self._build_results_tab(), t("hosts_sub_results"))
        self._sub_tabs.addTab(self._build_isolate_tab(), t("hosts_sub_isolate"))
        self._sub_tabs.currentChanged.connect(self._on_tab_changed)
        rl.addWidget(self._sub_tabs, 1)

        outer.addWidget(right)
        outer.setSizes([250, 900])
        lay.addWidget(outer)

    # ── Tab 0: Статус ─────────────────────────────────────────────────────────

    def _build_status_tab(self) -> QWidget:
        w   = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(12)

        # Host details card
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px;}")
        cl = QVBoxLayout(card); cl.setContentsMargins(16, 14, 16, 14); cl.setSpacing(8)

        self._st_name   = QLabel("—")
        self._st_name.setStyleSheet("font-size:18px;font-weight:bold;color:#58a6ff;")
        self._st_name.setWordWrap(True)
        self._st_addr_lbl, self._st_addr     = self._detail_row(cl, t("hosts_addr_lbl"))
        self._st_seen_lbl, self._st_seen     = self._detail_row(cl, t("hosts_last_ping_lbl"))
        self._st_scan_lbl, self._st_scan     = self._detail_row(cl, t("hosts_last_scan_lbl"))
        self._st_status_lbl, self._st_status = self._detail_row(cl, t("hosts_status_lbl"))
        cl.insertWidget(0, self._st_name)
        lay.addWidget(card)

        # ── Metrics card ─────────────────────────────────────────────────
        self._grp_metrics = QGroupBox(t("hosts_met_card_title"))
        gm = QVBoxLayout(self._grp_metrics)
        gm.setSpacing(8)

        def _bar_row(label_key: str):
            row = QHBoxLayout()
            lbl = QLabel(t(label_key) + ":")
            lbl.setMinimumWidth(170)
            lbl.setStyleSheet("color:#6e7681;font-size:12px;")
            bar = QProgressBar()
            bar.setRange(0, 100); bar.setValue(0)
            bar.setFixedHeight(14); bar.setTextVisible(False)
            bar.setStyleSheet(
                "QProgressBar{border:1px solid #30363d;border-radius:4px;background:#0d1117;}"
                "QProgressBar::chunk{background:#3fb950;border-radius:3px;}"
            )
            val = QLabel("—")
            val.setMinimumWidth(120)
            val.setStyleSheet("color:#e6edf3;font-size:12px;")
            row.addWidget(lbl); row.addWidget(bar, 1); row.addWidget(val)
            return row, lbl, bar, val

        cpu_row,  self._met_cpu_lbl,  self._met_cpu_bar,  self._met_cpu_val  = _bar_row("hosts_met_cpu")
        ram_row,  self._met_ram_lbl,  self._met_ram_bar,  self._met_ram_val  = _bar_row("hosts_met_ram")
        disk_row, self._met_disk_lbl, self._met_disk_bar, self._met_disk_val = _bar_row("hosts_met_disk")
        gm.addLayout(cpu_row)
        gm.addLayout(ram_row)
        gm.addLayout(disk_row)

        self._met_os_lbl,     self._met_os     = self._detail_row(gm, t("hosts_met_os"))
        self._met_uptime_lbl, self._met_uptime = self._detail_row(gm, t("hosts_met_uptime"))
        self._met_users_lbl,  self._met_users  = self._detail_row(gm, t("hosts_met_users"))

        upd_row = QHBoxLayout()
        self._met_updated_lbl = QLabel(t("hosts_met_updated") + ": —")
        self._met_updated_lbl.setStyleSheet("color:#6e7681;font-size:11px;")
        self._btn_met_refresh = QPushButton(t("hosts_met_refresh"))
        self._btn_met_refresh.setObjectName("secondaryBtn")
        self._btn_met_refresh.setFixedHeight(28)
        self._btn_met_refresh.clicked.connect(self._fetch_info)
        upd_row.addWidget(self._met_updated_lbl)
        upd_row.addStretch()
        upd_row.addWidget(self._btn_met_refresh)
        gm.addLayout(upd_row)

        lay.addWidget(self._grp_metrics)
        # ─────────────────────────────────────────────────────────────────

        # Action buttons
        self._grp_act = QGroupBox(t("hosts_actions_grp"))
        ga = QHBoxLayout(self._grp_act); ga.setSpacing(8)
        self._btn_ping = QPushButton(t("hosts_ping_btn2"))
        self._btn_ping.setObjectName("secondaryBtn"); self._btn_ping.setFixedHeight(36)
        self._btn_ping.clicked.connect(self._ping_selected)
        self._btn_deploy = QPushButton(t("hosts_deploy_btn2"))
        self._btn_deploy.setObjectName("secondaryBtn"); self._btn_deploy.setFixedHeight(36)
        self._btn_deploy.clicked.connect(self._deploy)
        ga.addWidget(self._btn_ping); ga.addWidget(self._btn_deploy); ga.addStretch()
        lay.addWidget(self._grp_act)

        # Quick navigation
        self._grp_nav = QGroupBox(t("hosts_nav_grp"))
        gn = QHBoxLayout(self._grp_nav); gn.setSpacing(8)
        self._btn_go_file = QPushButton(t("hosts_go_file_btn"))
        self._btn_go_file.setObjectName("secondaryBtn"); self._btn_go_file.setFixedHeight(36)
        self._btn_go_file.clicked.connect(lambda: self._sub_tabs.setCurrentIndex(self._TAB_FILE))
        self._btn_go_mem = QPushButton(t("hosts_go_mem_btn"))
        self._btn_go_mem.setObjectName("secondaryBtn"); self._btn_go_mem.setFixedHeight(36)
        self._btn_go_mem.clicked.connect(lambda: self._sub_tabs.setCurrentIndex(self._TAB_MEMORY))
        self._btn_go_iso = QPushButton(t("hosts_go_iso_btn"))
        self._btn_go_iso.setObjectName("dangerBtn"); self._btn_go_iso.setFixedHeight(36)
        self._btn_go_iso.clicked.connect(lambda: self._sub_tabs.setCurrentIndex(self._TAB_ISOLATE))
        gn.addWidget(self._btn_go_file); gn.addWidget(self._btn_go_mem); gn.addWidget(self._btn_go_iso)
        lay.addWidget(self._grp_nav)

        lay.addStretch()
        return w

    def _set_bar_color(self, bar: QProgressBar, pct: float):
        if pct <= 60:
            color = "#3fb950"
        elif pct <= 85:
            color = "#d29922"
        else:
            color = "#f85149"
        bar.setStyleSheet(
            f"QProgressBar{{border:1px solid #30363d;border-radius:4px;"
            f"background:#0d1117;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
        )

    def _detail_row(self, parent_layout, label: str) -> tuple:
        row = QHBoxLayout()
        lbl = QLabel(label + ":")
        lbl.setMinimumWidth(130)
        lbl.setStyleSheet("color:#6e7681;font-size:12px;")
        val = QLabel("—")
        val.setStyleSheet("color:#e6edf3;font-size:12px;")
        val.setWordWrap(True)
        row.addWidget(lbl); row.addWidget(val, 1)
        parent_layout.addLayout(row)
        return lbl, val

    # ── Tab 1: Файловый скан ──────────────────────────────────────────────────

    _INNER_TAB_STYLE = (
        "QTabBar::tab{padding:7px 14px;font-size:11px;min-height:18px;}"
        "QTabBar::tab:selected{font-weight:bold;color:#58a6ff;}"
        "QTabBar::tab:!selected{color:#6e7681;}"
        "QTabWidget::pane{border:1px solid #21262d;border-radius:4px;margin-top:2px;}")

    def _build_file_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self._file_inner_tabs = QTabWidget()
        self._file_inner_tabs.setStyleSheet(self._INNER_TAB_STYLE)
        self._file_inner_tabs.addTab(self._build_file_scan_tab(),  t("hosts_inner_scan"))
        self._file_inner_tabs.addTab(self._build_file_rules_tab(), t("hosts_inner_rules"))
        lay.addWidget(self._file_inner_tabs, 1)
        return w

    def _build_file_scan_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(6)

        self._grp_file_rules = QGroupBox(t("hosts_rules_grp"))
        gr = QVBoxLayout(self._grp_file_rules); gr.setSpacing(4)

        top_row = QHBoxLayout()
        self._btn_file_all  = QPushButton(t("hosts_btn_all"))
        self._btn_file_all.setObjectName("secondaryBtn")
        self._btn_file_none = QPushButton(t("hosts_btn_none"))
        self._btn_file_none.setObjectName("secondaryBtn")
        self._btn_file_all.clicked.connect(lambda: (
            self._toggle_rules(self._file_rule_list, True),
            self._update_rule_count(self._file_rule_list, self._file_rule_count)))
        self._btn_file_none.clicked.connect(lambda: (
            self._toggle_rules(self._file_rule_list, False),
            self._update_rule_count(self._file_rule_list, self._file_rule_count)))
        self._file_rule_count = QLabel("0 / 0 правил")
        self._file_rule_count.setStyleSheet("color:#6e7681;font-size:11px;")
        self._file_rule_filter = QLineEdit()
        self._file_rule_filter.setPlaceholderText("🔍  Поиск...")
        self._file_rule_filter.textChanged.connect(self._filter_file_rules)
        top_row.addWidget(self._btn_file_all); top_row.addWidget(self._btn_file_none)
        top_row.addWidget(self._file_rule_filter, 1)
        top_row.addWidget(self._file_rule_count)
        gr.addLayout(top_row)

        self._file_rule_list = QListWidget()
        _saved_file = set(Config.get("saved_file_rules") or [])
        for name in BUILTIN_YARA_RULES:
            item = QListWidgetItem(name)
            item.setCheckState(
                Qt.CheckState.Checked if name in _saved_file else Qt.CheckState.Unchecked)
            self._file_rule_list.addItem(item)
        self._file_rule_list.itemChanged.connect(
            lambda: (self._update_rule_count(self._file_rule_list, self._file_rule_count),
                     self._save_rule_selections()))
        self._update_rule_count(self._file_rule_list, self._file_rule_count)
        gr.addWidget(self._file_rule_list)
        lay.addWidget(self._grp_file_rules, 1)

        opt_row = QHBoxLayout()
        self._chk_ioc    = QCheckBox(t("hosts_ioc_chk")); self._chk_ioc.setChecked(True)
        self._chk_hashes = QCheckBox(t("hosts_hashes_chk"))
        self._lbl_path   = QLabel(t("hosts_path_remote"))
        self._path_inp   = QLineEdit(); self._path_inp.setText(r"C:\Users")
        self._path_inp.setToolTip(
            "Путь к папке на удалённом хосте.\n"
            "Вводите вручную — браузер открывает локальные папки.")
        btn_browse = QPushButton("..."); btn_browse.setObjectName("secondaryBtn")
        btn_browse.setFixedWidth(32)
        btn_browse.setToolTip("Открыть локальный браузер (для справки)")
        btn_browse.clicked.connect(self._browse_path)
        opt_row.addWidget(self._chk_ioc); opt_row.addWidget(self._chk_hashes)
        opt_row.addWidget(self._lbl_path); opt_row.addWidget(self._path_inp)
        opt_row.addWidget(btn_browse)
        lay.addLayout(opt_row)

        self._file_prog = QProgressBar()
        self._file_prog.setRange(0, 0); self._file_prog.setFixedHeight(4)
        self._file_prog.setVisible(False)
        lay.addWidget(self._file_prog)

        self._file_status = QLabel(t("hosts_file_status_hint"))
        self._file_status.setStyleSheet("color:#6e7681;font-size:11px;")
        lay.addWidget(self._file_status)

        scan_row = QHBoxLayout()
        self._btn_scan = QPushButton(t("hosts_scan_btn2")); self._btn_scan.setFixedHeight(38)
        self._btn_scan.clicked.connect(self._start_file_scan)
        self._btn_scan_stop = QPushButton(t("hosts_stop_btn"))
        self._btn_scan_stop.setObjectName("dangerBtn")
        self._btn_scan_stop.setFixedHeight(38)
        self._btn_scan_stop.setEnabled(False)
        self._btn_scan_stop.clicked.connect(self._stop_file_scan)
        scan_row.addWidget(self._btn_scan); scan_row.addWidget(self._btn_scan_stop)
        lay.addLayout(scan_row)
        return w

    def _build_file_rules_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(8)

        self._file_custom_hdr = QLabel(t("hosts_custom_rules_hdr"))
        self._file_custom_hdr.setStyleSheet("color:#6e7681;font-size:11px;font-weight:bold;letter-spacing:0.5px;")
        lay.addWidget(self._file_custom_hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: list of added custom rules ─────────────────────────────
        left_w = QWidget(); ll = QVBoxLayout(left_w)
        ll.setContentsMargins(0, 0, 6, 0); ll.setSpacing(4)
        self._file_added_rules_lbl = QLabel(t("hosts_added_rules_lbl"))
        self._file_added_rules_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        ll.addWidget(self._file_added_rules_lbl)
        self._file_custom_list = QListWidget()
        self._file_custom_list.setStyleSheet(
            "QListWidget{background:#0a0e14;border:1px solid #21262d;border-radius:4px;}")
        self._file_custom_list.currentRowChanged.connect(
            lambda _: self._load_custom_rule(
                self._file_custom_list, self._file_rule_edit, self._file_custom_rules))
        ll.addWidget(self._file_custom_list, 1)
        self._btn_del_file_rule = QPushButton(t("hosts_del_rule_btn"))
        self._btn_del_file_rule.setObjectName("dangerBtn"); self._btn_del_file_rule.setFixedHeight(30)
        self._btn_del_file_rule.clicked.connect(lambda: (
            self._delete_custom_rule(
                self._file_custom_list, self._file_rule_list,
                self._shared_custom_rules, self._file_rule_edit),
            self._sync_rule_lists()))
        ll.addWidget(self._btn_del_file_rule)
        splitter.addWidget(left_w)

        # ── Right: code editor ───────────────────────────────────────────
        right_w = QWidget(); rl = QVBoxLayout(right_w)
        rl.setContentsMargins(6, 0, 0, 0); rl.setSpacing(4)
        self._file_rule_editor_lbl = QLabel(t("hosts_rule_editor_lbl"))
        self._file_rule_editor_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        rl.addWidget(self._file_rule_editor_lbl)
        self._file_rule_edit = QTextEdit()
        self._file_rule_edit.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:4px;"
            "font-family:Consolas,monospace;font-size:12px;color:#c9d1d9;padding:8px;")
        self._file_rule_edit.setPlaceholderText(
            "rule MyRule {\n"
            "    meta:\n"
            "        description = \"My custom rule\"\n"
            "    strings:\n"
            "        $s1 = \"pattern\" ascii nocase\n"
            "    condition:\n"
            "        any of them\n"
            "}")
        rl.addWidget(self._file_rule_edit, 1)

        self._btn_add_file_rule = QPushButton(t("hosts_add_rule_btn"))
        self._btn_add_file_rule.setObjectName("secondaryBtn"); self._btn_add_file_rule.setFixedHeight(34)
        self._btn_add_file_rule.clicked.connect(lambda: (
            self._add_custom_rule(
                self._file_rule_edit, self._file_rule_list,
                self._shared_custom_rules, self._file_custom_list),
            self._sync_rule_lists()))
        rl.addWidget(self._btn_add_file_rule)
        splitter.addWidget(right_w)

        splitter.setSizes([200, 520])
        lay.addWidget(splitter, 1)
        return w

    # ── Tab 2: Memory Scan ────────────────────────────────────────────────────

    def _build_memory_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self._mem_inner_tabs = QTabWidget()
        self._mem_inner_tabs.setStyleSheet(self._INNER_TAB_STYLE)
        self._mem_inner_tabs.addTab(self._build_mem_scan_tab(),  t("hosts_mem_inner_scan"))
        self._mem_inner_tabs.addTab(self._build_mem_rules_tab(), t("hosts_mem_inner_rules"))
        lay.addWidget(self._mem_inner_tabs, 1)
        return w

    def _build_mem_scan_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(6)

        proc_ctrl = QHBoxLayout()
        self._btn_refresh_procs = QPushButton("⟳  Обновить процессы")
        self._btn_refresh_procs.setObjectName("secondaryBtn"); self._btn_refresh_procs.setFixedHeight(32)
        self._btn_refresh_procs.clicked.connect(self._refresh_remote_procs)
        self._mem_filter = QLineEdit(); self._mem_filter.setPlaceholderText("Фильтр по процессу...")
        self._mem_filter.textChanged.connect(self._filter_remote_procs)
        self._mem_proc_count = QLabel("0 процессов")
        self._mem_proc_count.setStyleSheet("color:#6e7681;font-size:11px;")
        proc_ctrl.addWidget(self._btn_refresh_procs)
        proc_ctrl.addWidget(self._mem_filter, 1)
        proc_ctrl.addWidget(self._mem_proc_count)
        lay.addLayout(proc_ctrl)

        grp_procs = QGroupBox("ПРОЦЕССЫ УДАЛЁННОГО ХОСТА")
        gp = QVBoxLayout(grp_procs); gp.setContentsMargins(8, 6, 8, 6)
        self._mem_proc_tbl = QTableWidget(0, 3)
        self._mem_proc_tbl.setHorizontalHeaderLabels(["PID", "Имя", "Путь к EXE"])
        self._mem_proc_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._mem_proc_tbl.horizontalHeader().resizeSection(0, 55)
        self._mem_proc_tbl.horizontalHeader().resizeSection(1, 150)
        self._mem_proc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._mem_proc_tbl.setMaximumHeight(130)
        gp.addWidget(self._mem_proc_tbl)
        lay.addWidget(grp_procs)

        grp_rules_m = QGroupBox("YARA ПРАВИЛА ДЛЯ ПАМЯТИ")
        gr_m = QVBoxLayout(grp_rules_m); gr_m.setSpacing(4)
        top_row_m = QHBoxLayout()
        btn_all_m  = QPushButton("Все");    btn_all_m.setObjectName("secondaryBtn")
        btn_none_m = QPushButton("Ничего"); btn_none_m.setObjectName("secondaryBtn")
        btn_all_m.clicked.connect(lambda: (
            self._toggle_rules(self._mem_rule_list, True),
            self._update_rule_count(self._mem_rule_list, self._mem_rule_count)))
        btn_none_m.clicked.connect(lambda: (
            self._toggle_rules(self._mem_rule_list, False),
            self._update_rule_count(self._mem_rule_list, self._mem_rule_count)))
        self._mem_rule_count = QLabel("0 / 0 правил")
        self._mem_rule_count.setStyleSheet("color:#6e7681;font-size:11px;")
        self._mem_rule_filter = QLineEdit()
        self._mem_rule_filter.setPlaceholderText("🔍  Поиск...")
        self._mem_rule_filter.textChanged.connect(self._filter_mem_rules)
        top_row_m.addWidget(btn_all_m); top_row_m.addWidget(btn_none_m)
        top_row_m.addWidget(self._mem_rule_filter, 1)
        top_row_m.addWidget(self._mem_rule_count)
        gr_m.addLayout(top_row_m)

        self._mem_rule_list = QListWidget()
        _saved_mem = set(Config.get("saved_mem_rules") or [])
        for name in BUILTIN_YARA_RULES:
            item = QListWidgetItem(name)
            item.setCheckState(
                Qt.CheckState.Checked if name in _saved_mem else Qt.CheckState.Unchecked)
            self._mem_rule_list.addItem(item)
        self._mem_rule_list.itemChanged.connect(
            lambda: (self._update_rule_count(self._mem_rule_list, self._mem_rule_count),
                     self._save_rule_selections()))
        self._update_rule_count(self._mem_rule_list, self._mem_rule_count)
        gr_m.addWidget(self._mem_rule_list)
        lay.addWidget(grp_rules_m, 1)

        self._mem_prog = QProgressBar()
        self._mem_prog.setRange(0, 0); self._mem_prog.setFixedHeight(4)
        self._mem_prog.setVisible(False)
        lay.addWidget(self._mem_prog)

        self._mem_status = QLabel("Нажмите «Обновить процессы», выберите правила и «Scan Memory»")
        self._mem_status.setStyleSheet("color:#6e7681;font-size:11px;")
        self._mem_status.setWordWrap(True)
        lay.addWidget(self._mem_status)

        scan_row = QHBoxLayout()
        self._btn_mem_scan = QPushButton("▶  Scan Memory"); self._btn_mem_scan.setFixedHeight(38)
        self._btn_mem_scan.clicked.connect(self._start_mem_scan)
        self._btn_mem_stop = QPushButton("⏹  Стоп"); self._btn_mem_stop.setObjectName("dangerBtn")
        self._btn_mem_stop.setFixedWidth(90); self._btn_mem_stop.setEnabled(False)
        self._btn_mem_stop.clicked.connect(self._stop_mem_scan)
        scan_row.addWidget(self._btn_mem_scan); scan_row.addWidget(self._btn_mem_stop)
        lay.addLayout(scan_row)
        return w

    def _build_mem_rules_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(8)

        self._mem_custom_hdr = QLabel(t("hosts_custom_rules_hdr"))
        self._mem_custom_hdr.setStyleSheet("color:#6e7681;font-size:11px;font-weight:bold;letter-spacing:0.5px;")
        lay.addWidget(self._mem_custom_hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: list of added custom rules ─────────────────────────────
        left_w = QWidget(); ll = QVBoxLayout(left_w)
        ll.setContentsMargins(0, 0, 6, 0); ll.setSpacing(4)
        self._mem_added_rules_lbl = QLabel(t("hosts_added_rules_lbl"))
        self._mem_added_rules_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        ll.addWidget(self._mem_added_rules_lbl)
        self._mem_custom_list = QListWidget()
        self._mem_custom_list.setStyleSheet(
            "QListWidget{background:#0a0e14;border:1px solid #21262d;border-radius:4px;}")
        self._mem_custom_list.currentRowChanged.connect(
            lambda _: self._load_custom_rule(
                self._mem_custom_list, self._mem_rule_edit, self._mem_custom_rules))
        ll.addWidget(self._mem_custom_list, 1)
        self._btn_del_mem_rule = QPushButton(t("hosts_del_rule_btn"))
        self._btn_del_mem_rule.setObjectName("dangerBtn"); self._btn_del_mem_rule.setFixedHeight(30)
        self._btn_del_mem_rule.clicked.connect(lambda: (
            self._delete_custom_rule(
                self._mem_custom_list, self._mem_rule_list,
                self._shared_custom_rules, self._mem_rule_edit),
            self._sync_rule_lists()))
        ll.addWidget(self._btn_del_mem_rule)
        splitter.addWidget(left_w)

        # ── Right: code editor ───────────────────────────────────────────
        right_w = QWidget(); rl = QVBoxLayout(right_w)
        rl.setContentsMargins(6, 0, 0, 0); rl.setSpacing(4)
        self._mem_rule_editor_lbl = QLabel(t("hosts_rule_editor_lbl"))
        self._mem_rule_editor_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        rl.addWidget(self._mem_rule_editor_lbl)
        self._mem_rule_edit = QTextEdit()
        self._mem_rule_edit.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:4px;"
            "font-family:Consolas,monospace;font-size:12px;color:#c9d1d9;padding:8px;")
        self._mem_rule_edit.setPlaceholderText(
            "rule MyRule {\n"
            "    meta:\n"
            "        description = \"My custom rule\"\n"
            "    strings:\n"
            "        $s1 = \"pattern\" ascii nocase\n"
            "    condition:\n"
            "        any of them\n"
            "}")
        rl.addWidget(self._mem_rule_edit, 1)

        self._btn_add_mem_rule = QPushButton(t("hosts_add_rule_btn"))
        self._btn_add_mem_rule.setObjectName("secondaryBtn"); self._btn_add_mem_rule.setFixedHeight(34)
        self._btn_add_mem_rule.clicked.connect(lambda: (
            self._add_custom_rule(
                self._mem_rule_edit, self._mem_rule_list,
                self._shared_custom_rules, self._mem_custom_list),
            self._sync_rule_lists()))
        rl.addWidget(self._btn_add_mem_rule)
        splitter.addWidget(right_w)

        splitter.setSizes([200, 520])
        lay.addWidget(splitter, 1)
        return w

    # ── Tab 3: Результаты ─────────────────────────────────────────────────────

    def _build_results_tab(self) -> QWidget:
        w   = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(6)

        # Stats row
        stats_row = QHBoxLayout()
        self._lbl_result_stats = QLabel("Нет данных")
        self._lbl_result_stats.setStyleSheet("color:#6e7681;font-size:11px;")
        btn_clear_tbl = QPushButton("Очистить")
        btn_clear_tbl.setObjectName("secondaryBtn")
        btn_clear_tbl.clicked.connect(self._clear_results)
        self._btn_vt = QPushButton("🔍  Проверить в VT")
        self._btn_vt.setObjectName("secondaryBtn")
        self._btn_vt.setVisible(False)
        self._btn_vt.clicked.connect(self._start_vt_check)
        self._btn_export_csv = QPushButton("💾  CSV")
        self._btn_export_csv.setObjectName("secondaryBtn")
        self._btn_export_csv.setVisible(False)
        self._btn_export_csv.clicked.connect(self._export_hashes_csv)
        stats_row.addWidget(self._lbl_result_stats); stats_row.addStretch()
        stats_row.addWidget(self._btn_vt)
        stats_row.addWidget(self._btn_export_csv)
        stats_row.addWidget(btn_clear_tbl)
        lay.addLayout(stats_row)

        # Vertical splitter: table (top) + log (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["ТИП / ПРАВИЛО", "SEVERITY", "ФАЙЛ / ПРОЦЕСС"])
        self._tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tbl.horizontalHeader().resizeSection(0, 190)
        self._tbl.horizontalHeader().resizeSection(1, 80)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setStyleSheet("QTableWidget{alternate-background-color:#0f1318;}")
        splitter.addWidget(self._tbl)

        log_w = QWidget(); lw = QVBoxLayout(log_w); lw.setContentsMargins(0, 4, 0, 0)
        log_header = QHBoxLayout()
        lbl_log = QLabel("Лог сканирования")
        lbl_log.setStyleSheet("color:#6e7681;font-size:11px;font-weight:bold;")
        btn_clear_log = QPushButton("Очистить лог")
        btn_clear_log.setObjectName("secondaryBtn")
        btn_clear_log.clicked.connect(lambda: self._scan_log.clear())
        log_header.addWidget(lbl_log); log_header.addStretch(); log_header.addWidget(btn_clear_log)
        lw.addLayout(log_header)
        self._scan_log = QTextEdit()
        self._scan_log.setReadOnly(True)
        self._scan_log.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:6px;"
            "font-family:Consolas,monospace;font-size:11px;color:#c9d1d9;padding:6px;")
        lw.addWidget(self._scan_log)
        splitter.addWidget(log_w)

        splitter.setSizes([300, 200])
        lay.addWidget(splitter, 1)
        return w

    # ── Tab 4: Изоляция ───────────────────────────────────────────────────────

    def _build_isolate_tab(self) -> QWidget:
        w   = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(12)

        # Warning banner
        self._iso_warn = QLabel(t("hosts_iso_warn"))
        self._iso_warn.setWordWrap(True)
        self._iso_warn.setStyleSheet(
            "color:#d29922;font-size:12px;padding:10px 12px;"
            "background:#2d2208;border:1px solid #4d3800;border-radius:6px;")
        lay.addWidget(self._iso_warn)

        # Status card
        self._iso_status_card = QFrame()
        self._iso_status_card.setStyleSheet(
            "QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px;}")
        sc = QHBoxLayout(self._iso_status_card)
        sc.setContentsMargins(16, 12, 16, 12)
        self._iso_status_icon  = QLabel("●")
        self._iso_status_icon.setStyleSheet("font-size:24px;color:#6e7681;")
        self._iso_status_text  = QLabel(t("hosts_iso_unknown"))
        self._iso_status_text.setStyleSheet("font-size:14px;font-weight:bold;color:#8b949e;")
        sc.addWidget(self._iso_status_icon); sc.addWidget(self._iso_status_text); sc.addStretch()
        self._btn_iso_check = QPushButton(t("hosts_iso_check_btn"))
        self._btn_iso_check.setObjectName("secondaryBtn"); self._btn_iso_check.setFixedHeight(32)
        self._btn_iso_check.clicked.connect(self._check_isolation_status)
        sc.addWidget(self._btn_iso_check)
        lay.addWidget(self._iso_status_card)

        # Management IP
        self._grp_iso_mgmt = QGroupBox(t("hosts_iso_mgmt_grp"))
        gi = QHBoxLayout(self._grp_iso_mgmt)
        self._iso_mgmt_ip = QLineEdit()
        self._iso_mgmt_ip.setPlaceholderText(t("hosts_iso_mgmt_ph"))
        self._iso_mgmt_ip.setText(_local_ip())
        gi.addWidget(self._iso_mgmt_ip)
        lay.addWidget(self._grp_iso_mgmt)

        # Action buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        self._btn_isolate = QPushButton(t("hosts_iso_btn"))
        self._btn_isolate.setObjectName("dangerBtn"); self._btn_isolate.setFixedHeight(42)
        self._btn_isolate.clicked.connect(self._isolate_host)
        self._btn_restore = QPushButton(t("hosts_restore_btn"))
        self._btn_restore.setObjectName("secondaryBtn"); self._btn_restore.setFixedHeight(42)
        self._btn_restore.clicked.connect(self._restore_host)
        btn_row.addWidget(self._btn_isolate); btn_row.addWidget(self._btn_restore)
        lay.addLayout(btn_row)

        # Isolation log
        self._grp_iso_log = QGroupBox(t("hosts_iso_log_grp"))
        gl = QVBoxLayout(self._grp_iso_log)
        self._iso_log = QTextEdit()
        self._iso_log.setReadOnly(True)
        self._iso_log.setMaximumHeight(160)
        self._iso_log.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:6px;"
            "font-family:Consolas,monospace;font-size:11px;color:#c9d1d9;padding:6px;")
        gl.addWidget(self._iso_log)
        lay.addWidget(self._grp_iso_log)
        lay.addStretch()
        return w

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def _log(self, msg: str, color: str = "#8b949e"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._scan_log.append(
            f'<span style="color:#484f58">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>')

    def _iso_log_append(self, msg: str, color: str = "#8b949e"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._iso_log.append(
            f'<span style="color:#484f58">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>')

    def _clear_results(self):
        if self._vt_worker and self._vt_worker.isRunning():
            self._vt_worker.stop()
        self._tbl.setRowCount(0)
        self._scan_log.clear()
        self._lbl_result_stats.setText("Нет данных")
        self._hash_row_map = {}
        self._vt_results = []
        self._btn_vt.setVisible(False)
        self._btn_export_csv.setVisible(False)

    @staticmethod
    def _toggle_rules(rule_list: QListWidget, checked: bool) -> None:
        rule_list.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(rule_list.count()):
            rule_list.item(i).setCheckState(state)
        rule_list.blockSignals(False)

    @staticmethod
    def _update_rule_count(rule_list: QListWidget, label: QLabel) -> None:
        checked = sum(
            1 for i in range(rule_list.count())
            if rule_list.item(i).checkState() == Qt.CheckState.Checked)
        total = rule_list.count()
        label.setText(f"{checked} / {total} правил")
        label.setStyleSheet(
            f"font-size:11px;color:{'#3fb950' if checked else '#6e7681'};")

    @staticmethod
    def _get_selected_rules(rule_list: QListWidget, custom_rules: dict) -> dict:
        selected = {}
        for i in range(rule_list.count()):
            item = rule_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                name = item.text()
                if name in BUILTIN_YARA_RULES:
                    selected[name] = BUILTIN_YARA_RULES[name]
                elif name in custom_rules:
                    selected[name] = custom_rules[name]
        return selected

    def _add_custom_rule(self, text_edit: QTextEdit, rule_list: QListWidget,
                         custom_store: dict,
                         custom_list: "QListWidget | None" = None) -> None:
        text = text_edit.toPlainText().strip()
        if not text:
            return
        m    = re.search(r'rule\s+(\w+)', text)
        name = m.group(1) if m else f"Custom_{len(custom_store) + 1}"
        custom_store[name] = text
        # Update existing item in rule_list (for re-add / update)
        for i in range(rule_list.count()):
            if rule_list.item(i).text() == name:
                if custom_list:
                    self._refresh_custom_list(custom_list, custom_store)
                text_edit.clear()
                return
        item = QListWidgetItem(name)
        item.setCheckState(Qt.CheckState.Checked)
        item.setForeground(QColor("#58a6ff"))
        rule_list.addItem(item)
        if custom_list:
            self._refresh_custom_list(custom_list, custom_store)
        text_edit.clear()

    def _refresh_custom_list(self, custom_list: QListWidget,
                              custom_store: dict) -> None:
        custom_list.blockSignals(True)
        custom_list.clear()
        for name in custom_store:
            item = QListWidgetItem(name)
            item.setForeground(QColor("#58a6ff"))
            custom_list.addItem(item)
        custom_list.blockSignals(False)

    def _load_custom_rule(self, custom_list: QListWidget,
                          editor: QTextEdit, custom_store: dict) -> None:
        item = custom_list.currentItem()
        if not item:
            return
        text = custom_store.get(item.text(), "")
        if text:
            editor.blockSignals(True)
            editor.setPlainText(text)
            editor.blockSignals(False)

    def _delete_custom_rule(self, custom_list: QListWidget,
                            rule_list: QListWidget,
                            custom_store: dict, editor: QTextEdit) -> None:
        item = custom_list.currentItem()
        if not item:
            return
        name = item.text()
        custom_store.pop(name, None)
        for i in range(rule_list.count()):
            if rule_list.item(i).text() == name:
                rule_list.takeItem(i)
                break
        custom_list.takeItem(custom_list.currentRow())
        editor.clear()

    def _save_rule_selections(self):
        file_checked = [self._file_rule_list.item(i).text()
                        for i in range(self._file_rule_list.count())
                        if self._file_rule_list.item(i).checkState() == Qt.CheckState.Checked
                        and self._file_rule_list.item(i).text() in BUILTIN_YARA_RULES]
        mem_checked  = [self._mem_rule_list.item(i).text()
                        for i in range(self._mem_rule_list.count())
                        if self._mem_rule_list.item(i).checkState() == Qt.CheckState.Checked
                        and self._mem_rule_list.item(i).text() in BUILTIN_YARA_RULES]
        Config.set("saved_file_rules", file_checked)
        Config.set("saved_mem_rules",  mem_checked)

    def _sync_rule_lists(self):
        """Ensure both file and memory rule lists contain all shared custom rules."""
        for name in self._shared_custom_rules:
            for rule_list in (self._file_rule_list, self._mem_rule_list):
                if not any(rule_list.item(i).text() == name
                           for i in range(rule_list.count())):
                    item = QListWidgetItem(name)
                    item.setCheckState(Qt.CheckState.Checked)
                    item.setForeground(QColor("#58a6ff"))
                    rule_list.addItem(item)
        self._refresh_custom_list(self._file_custom_list, self._shared_custom_rules)
        self._refresh_custom_list(self._mem_custom_list,  self._shared_custom_rules)

    def _filter_file_rules(self, text: str) -> None:
        lo = text.lower()
        for i in range(self._file_rule_list.count()):
            item = self._file_rule_list.item(i)
            item.setHidden(bool(lo) and lo not in item.text().lower())

    def _filter_mem_rules(self, text: str) -> None:
        lo = text.lower()
        for i in range(self._mem_rule_list.count()):
            item = self._mem_rule_list.item(i)
            item.setHidden(bool(lo) and lo not in item.text().lower())

    def _browse_path(self):
        QMessageBox.information(
            self, "Локальный путь",
            "Диалог открывает локальные папки.\n"
            "Убедитесь, что выбранный путь существует на удалённом хосте.")
        d = QFileDialog.getExistingDirectory(self, "Выберите директорию")
        if d:
            self._path_inp.setText(d)

    def retranslate(self, _lang: str = ""):
        self._btn_add.setText(t("hosts_add_btn"))
        self._btn_remove.setText(t("hosts_remove_btn"))
        self._btn_import_csv.setText(t("hosts_import_csv"))
        if self._showing_hint:
            self._info_label.setText(t("hosts_select_hint"))
        # Sub-tab titles
        self._sub_tabs.setTabText(self._TAB_STATUS,  t("hosts_sub_status"))
        self._sub_tabs.setTabText(self._TAB_FILE,    t("hosts_sub_file"))
        self._sub_tabs.setTabText(self._TAB_MEMORY,  t("hosts_sub_memory"))
        self._sub_tabs.setTabText(self._TAB_RESULTS, t("hosts_sub_results"))
        self._sub_tabs.setTabText(self._TAB_ISOLATE, t("hosts_sub_isolate"))
        # Status tab
        self._grp_act.setTitle(t("hosts_actions_grp"))
        self._grp_nav.setTitle(t("hosts_nav_grp"))
        self._btn_ping.setText(t("hosts_ping_btn2"))
        self._btn_deploy.setText(t("hosts_deploy_btn2"))
        self._btn_go_file.setText(t("hosts_go_file_btn"))
        self._btn_go_mem.setText(t("hosts_go_mem_btn"))
        self._btn_go_iso.setText(t("hosts_go_iso_btn"))
        self._st_addr_lbl.setText(t("hosts_addr_lbl") + ":")
        self._st_seen_lbl.setText(t("hosts_last_ping_lbl") + ":")
        self._st_scan_lbl.setText(t("hosts_last_scan_lbl") + ":")
        self._st_status_lbl.setText(t("hosts_status_lbl") + ":")
        # Metrics card
        self._grp_metrics.setTitle(t("hosts_met_card_title"))
        self._met_cpu_lbl.setText(t("hosts_met_cpu") + ":")
        self._met_ram_lbl.setText(t("hosts_met_ram") + ":")
        self._met_disk_lbl.setText(t("hosts_met_disk") + ":")
        self._met_os_lbl.setText(t("hosts_met_os") + ":")
        self._met_uptime_lbl.setText(t("hosts_met_uptime") + ":")
        self._met_users_lbl.setText(t("hosts_met_users") + ":")
        self._btn_met_refresh.setText(t("hosts_met_refresh"))
        # File scan tab
        self._grp_file_rules.setTitle(t("hosts_rules_grp"))
        self._btn_file_all.setText(t("hosts_btn_all"))
        self._btn_file_none.setText(t("hosts_btn_none"))
        self._chk_ioc.setText(t("hosts_ioc_chk"))
        self._chk_hashes.setText(t("hosts_hashes_chk"))
        self._lbl_path.setText(t("hosts_path_remote"))
        self._btn_scan.setText(t("hosts_scan_btn2"))
        self._btn_scan_stop.setText(t("hosts_stop_btn"))
        # Inner tab titles (File Scan)
        self._file_inner_tabs.setTabText(0, t("hosts_inner_scan"))
        self._file_inner_tabs.setTabText(1, t("hosts_inner_rules"))
        # File custom rules panel
        self._file_custom_hdr.setText(t("hosts_custom_rules_hdr"))
        self._file_added_rules_lbl.setText(t("hosts_added_rules_lbl"))
        self._file_rule_editor_lbl.setText(t("hosts_rule_editor_lbl"))
        self._btn_del_file_rule.setText(t("hosts_del_rule_btn"))
        self._btn_add_file_rule.setText(t("hosts_add_rule_btn"))
        # Inner tab titles (Memory Scan)
        self._mem_inner_tabs.setTabText(0, t("hosts_mem_inner_scan"))
        self._mem_inner_tabs.setTabText(1, t("hosts_mem_inner_rules"))
        # Memory custom rules panel
        self._mem_custom_hdr.setText(t("hosts_custom_rules_hdr"))
        self._mem_added_rules_lbl.setText(t("hosts_added_rules_lbl"))
        self._mem_rule_editor_lbl.setText(t("hosts_rule_editor_lbl"))
        self._btn_del_mem_rule.setText(t("hosts_del_rule_btn"))
        self._btn_add_mem_rule.setText(t("hosts_add_rule_btn"))
        # Results table headers
        self._tbl.setHorizontalHeaderLabels([
            t("hosts_tbl_type_hdr"), t("hosts_tbl_sev_hdr"), t("hosts_tbl_file_hdr")])
        # Isolation tab
        self._iso_warn.setText(t("hosts_iso_warn"))
        self._btn_iso_check.setText(t("hosts_iso_check_btn"))
        self._grp_iso_mgmt.setTitle(t("hosts_iso_mgmt_grp"))
        self._iso_mgmt_ip.setPlaceholderText(t("hosts_iso_mgmt_ph"))
        self._btn_isolate.setText(t("hosts_iso_btn"))
        self._btn_restore.setText(t("hosts_restore_btn"))
        self._grp_iso_log.setTitle(t("hosts_iso_log_grp"))
        self._reload_hosts()

    # ── Host list ──────────────────────────────────────────────────────────────

    def _reload_hosts(self):
        self._host_list.clear()
        hosts = load_hosts()
        self._lbl_count.setText(t("hosts_count", n=len(hosts)))
        for h in hosts:
            item = QListWidgetItem(f"🖥  {h['name']}\n{h['ip']}:{h['port']}")
            item.setData(Qt.ItemDataRole.UserRole, h)
            self._host_list.addItem(item)

    def _on_host_select(self, row: int):
        self._mem_proc_tbl.setRowCount(0)
        self._remote_procs = []
        self._mem_proc_count.setText("0 процессов")
        self._clear_results()

        if row < 0:
            self._selected_id = None
            self._btn_remove.setEnabled(False)
            self._sub_tabs.setEnabled(False)
            self._showing_hint = True
            self._info_label.setText(t("hosts_select_hint"))
            self._stop_info_timer()
            return

        host = self._host_list.item(row).data(Qt.ItemDataRole.UserRole)
        self._selected_id = host["id"]
        self._btn_remove.setEnabled(True)
        self._sub_tabs.setEnabled(True)
        self._showing_hint = False

        seen = host.get("last_seen") or t("hosts_never")
        scan = host.get("last_scan") or t("hosts_never")
        self._info_label.setText(
            f"<b style='color:#58a6ff'>{host['name']}</b>"
            f"  ·  <span style='color:#8b949e'>{host['ip']}:{host['port']}</span>"
            f"  ·  ping: {seen}  ·  {t('hosts_info_scan')}: {scan}")
        self._update_status_tab(host)
        if self._sub_tabs.currentIndex() == self._TAB_STATUS:
            self._start_info_timer()
        if self._on_host_changed:
            self._on_host_changed(host)

    def _update_status_tab(self, host: dict):
        self._st_name.setText(host.get("name", "—"))
        self._st_addr.setText(f"{host.get('ip','—')}:{host.get('port','—')}")
        self._st_seen.setText(host.get("last_seen") or t("hosts_never"))
        self._st_scan.setText(host.get("last_scan") or t("hosts_never"))
        self._st_status.setText("⟳ Status...")

    def _on_tab_changed(self, idx: int):
        if idx == self._TAB_STATUS and self._selected_id:
            self._start_info_timer()
        else:
            self._stop_info_timer()
        if idx == self._TAB_ISOLATE:
            self._check_isolation_status()

    def _add_host(self):
        dlg = _AddHostDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        d = dlg.data()
        if not d["ip"] or not d["name"]:
            QMessageBox.warning(self, t("error"), t("hosts_add_error")); return
        add_host(d["name"], d["ip"], d["port"], d["token"])
        self._reload_hosts()
        if self._on_hosts_list_changed:
            self._on_hosts_list_changed()

    def _import_hosts_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт хостов из CSV", "", "CSV файлы (*.csv);;Все файлы (*)")
        if not path:
            return

        added = skipped = errors = 0
        existing_ips = {h["ip"] for h in load_hosts()}

        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                required = {"name", "ip", "port", "token"}
                if not required.issubset(set(reader.fieldnames or [])):
                    QMessageBox.critical(
                        self, "Ошибка формата",
                        "CSV должен содержать заголовки: name, ip, port, token")
                    return
                for row in reader:
                    ip = row.get("ip", "").strip()
                    name = row.get("name", "").strip()
                    if not ip or not name:
                        errors += 1
                        continue
                    if ip in existing_ips:
                        skipped += 1
                        continue
                    try:
                        port = int(row.get("port", "5555").strip())
                    except ValueError:
                        port = 5555
                    token = row.get("token", "").strip()
                    add_host(name, ip, port, token)
                    existing_ips.add(ip)
                    added += 1
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать CSV:\n{e}")
            return

        self._reload_hosts()
        if self._on_hosts_list_changed:
            self._on_hosts_list_changed()

        QMessageBox.information(
            self, "Импорт завершён",
            f"Добавлено: {added}\nПропущено (уже есть): {skipped}\nОшибок строк: {errors}")

    def _remove_host(self):
        if not self._selected_id:
            return
        if QMessageBox.question(
            self, t("hosts_remove_confirm_title"), t("hosts_remove_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        remove_host(self._selected_id)
        self._selected_id = None
        if self._on_host_changed:
            self._on_host_changed(None)
        self._reload_hosts()
        if self._on_hosts_list_changed:
            self._on_hosts_list_changed()

    # ── Ping ──────────────────────────────────────────────────────────────────

    def _start_ping_timer(self):
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._ping_all)
        self._ping_timer.start(30_000)

    def _ping_all(self):
        hosts = load_hosts()
        if not hosts or (self._ping_worker and self._ping_worker.isRunning()):
            return
        self._ping_worker = PingWorker(hosts)
        self._ping_worker.result.connect(self._on_ping_result)
        self._ping_worker.start()

    def _ping_selected(self):
        if not self._selected_id or (self._ping_worker and self._ping_worker.isRunning()):
            return
        hosts = [h for h in load_hosts() if h["id"] == self._selected_id]
        if not hosts:
            return
        self._st_status.setText("⟳ Пинг...")
        self._ping_worker = PingWorker(hosts)
        self._ping_worker.result.connect(self._on_ping_result)
        self._ping_worker.start()

    def _on_ping_result(self, host_id: str, online: bool, info: dict):
        ts = datetime.now().strftime("%H:%M:%S")
        if online:
            update_host(host_id, last_seen=ts)
        for i in range(self._host_list.count()):
            item = self._host_list.item(i)
            h    = item.data(Qt.ItemDataRole.UserRole)
            if h["id"] != host_id:
                continue
            if online:
                h["last_seen"] = ts
            item.setData(Qt.ItemDataRole.UserRole, h)
            item.setForeground(QColor("#3fb950") if online else QColor("#f85149"))
            item.setText(
                f"🖥  {h['name']}\n{h['ip']}:{h['port']}  "
                f"{'● online' if online else '● offline'}")
            if h["id"] == self._selected_id:
                ping_str = ts if online else "✗ offline"
                self._st_seen.setText(ping_str)
                self._st_status.setText(
                    f"<span style='color:#3fb950'>● Online</span>" if online
                    else "<span style='color:#f85149'>✗ Offline</span>")
                scan = h.get("last_scan") or t("hosts_never")
                self._info_label.setText(
                    f"<b style='color:#58a6ff'>{h['name']}</b>"
                    f"  ·  <span style='color:#8b949e'>{h['ip']}:{h['port']}</span>"
                    f"  ·  ping: {ping_str}  ·  {t('hosts_info_scan')}: {scan}")
            break

    def _get_selected_host(self) -> dict | None:
        for i in range(self._host_list.count()):
            h = self._host_list.item(i).data(Qt.ItemDataRole.UserRole)
            if h["id"] == self._selected_id:
                return h
        return None

    # ── Info (live metrics) ────────────────────────────────────────────────

    def _start_info_timer(self):
        if not hasattr(self, "_info_timer"):
            self._info_timer = QTimer(self)
            self._info_timer.timeout.connect(self._fetch_info)
        self._fetch_info()
        self._info_timer.start(30_000)

    def _stop_info_timer(self):
        if hasattr(self, "_info_timer"):
            self._info_timer.stop()

    def _fetch_info(self):
        if not self._selected_id:
            return
        if self._info_worker and self._info_worker.isRunning():
            return
        hosts = [h for h in load_hosts() if h["id"] == self._selected_id]
        if not hosts:
            return
        self._info_worker = RemoteInfoWorker(hosts[0])
        self._info_worker.done.connect(self._on_info_done)
        self._info_worker.error.connect(self._on_info_error)
        self._info_worker.start()

    def _on_info_done(self, data: dict):
        import time
        from datetime import timedelta

        cpu      = data.get("cpu_percent", 0)
        ram_pct  = data.get("ram_percent", 0)
        ram_used = data.get("ram_used",  0) / 1024**3
        ram_tot  = data.get("ram_total", 1) / 1024**3
        dsk_pct  = data.get("disk_percent", 0)
        dsk_used = data.get("disk_used",  0) / 1024**3
        dsk_tot  = data.get("disk_total", 1) / 1024**3
        boot     = data.get("boot_time", 0)
        os_str   = data.get("os", "—")
        users    = data.get("users", [])

        self._met_cpu_bar.setValue(int(cpu))
        self._met_cpu_val.setText(f"{cpu:.1f} %")
        self._set_bar_color(self._met_cpu_bar, cpu)

        self._met_ram_bar.setValue(int(ram_pct))
        self._met_ram_val.setText(f"{ram_used:.1f} / {ram_tot:.1f} GB")
        self._set_bar_color(self._met_ram_bar, ram_pct)

        self._met_disk_bar.setValue(int(dsk_pct))
        self._met_disk_val.setText(f"{dsk_used:.1f} / {dsk_tot:.1f} GB")
        self._set_bar_color(self._met_disk_bar, dsk_pct)

        self._met_os.setText(os_str)

        if boot:
            td   = timedelta(seconds=time.time() - boot)
            h, r = divmod(td.seconds, 3600)
            m    = r // 60
            self._met_uptime.setText(f"{td.days}d {h:02d}:{m:02d}")
        else:
            self._met_uptime.setText("—")

        self._met_users.setText(", ".join(users) if users else "—")
        self._met_updated_lbl.setText(
            f"{t('hosts_met_updated')}: {datetime.now().strftime('%H:%M:%S')}")

    def _on_info_error(self, _msg: str):
        for bar in (self._met_cpu_bar, self._met_ram_bar, self._met_disk_bar):
            bar.setValue(0)
            self._set_bar_color(bar, 0)
        for lbl in (self._met_cpu_val, self._met_ram_val, self._met_disk_val,
                    self._met_os, self._met_uptime, self._met_users):
            lbl.setText("—")
        self._met_updated_lbl.setText(t("hosts_met_updated") + ": ✗")

    # ── File scan ─────────────────────────────────────────────────────────────

    def _start_file_scan(self):
        host = self._get_selected_host()
        if not host:
            return
        if self._scan_worker and self._scan_worker.isRunning():
            self._file_status.setText("⚠ Сканирование уже выполняется"); return

        rules = self._get_selected_rules(self._file_rule_list, self._file_custom_rules)
        scan_types = []
        if rules:               scan_types.append("yara")
        if self._chk_ioc.isChecked():    scan_types.append("ioc")
        if self._chk_hashes.isChecked(): scan_types.append("hashes")
        if not scan_types:
            self._file_status.setText("⚠ Выберите хотя бы одно правило YARA, IOC или Хэши")
            self._log("Сканирование не запущено: не выбраны типы проверок", "#d29922")
            return
        path = self._path_inp.text().strip()
        if not path:
            self._file_status.setText("⚠ Укажите путь"); return

        host_label = f"{host['name']} ({host['ip']})"
        self._log(
            f"▶ Файловый скан на <b>{host_label}</b>  "
            f"| YARA: {len(rules)}  | Путь: {path}", "#58a6ff")
        if rules:
            self._log(f"  Правила: {', '.join(list(rules.keys())[:8])}"
                      f"{'...' if len(rules) > 8 else ''}", "#6e7681")
        DashboardTab.log_event("SCAN",
            f"Файловый скан запущен — {host_label} | {path}",
            level="info", scan=True, host=host_label)

        self._btn_scan.setEnabled(False)
        self._btn_scan_stop.setEnabled(True)
        self._host_list.setEnabled(False)
        self._file_prog.setVisible(True)
        self._tbl.setRowCount(0)
        self._lbl_result_stats.setText("Сканирование...")
        self._file_status.setText(f"Подключение к {host['ip']}...")
        self._sub_tabs.setTabText(self._TAB_RESULTS, "📋  Результаты (сканирование...)")

        self._scan_worker = RemoteScanWorker(host, scan_types, path, rules)
        self._scan_worker.progress.connect(self._on_file_progress)
        self._scan_worker.done.connect(lambda r: self._on_results_done(r, host))
        self._scan_worker.error.connect(
            lambda m, hl=host_label: self._on_file_error(m, hl))
        self._scan_worker.finished.connect(self._on_file_finished)
        self._scan_worker.start()

    def _on_file_progress(self, msg: str):
        self._file_status.setText(msg)
        self._log(f"  {msg}", "#6e7681")

    def _on_file_error(self, msg: str, host_label: str = ""):
        self._file_status.setText(f"✘ {msg[:120]}")
        self._log(f"✘ Ошибка: {msg}", "#f85149")
        self._sub_tabs.setCurrentIndex(self._TAB_RESULTS)
        if host_label:
            DashboardTab.log_event("SCAN",
                f"✘ Ошибка сканирования — {host_label}: {msg[:80]}",
                level="high", scan=True, host=host_label)

    def _on_file_finished(self):
        self._btn_scan.setEnabled(True)
        self._btn_scan_stop.setEnabled(False)
        self._host_list.setEnabled(True)
        self._file_prog.setVisible(False)
        self._sub_tabs.setTabText(self._TAB_RESULTS, "📋  Результаты")

    def _stop_file_scan(self):
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.quit()
            self._scan_worker.wait(1000)
        self._btn_scan.setEnabled(True)
        self._btn_scan_stop.setEnabled(False)
        self._host_list.setEnabled(True)
        self._log("Сканирование остановлено", "#f85149")

    # ── Memory scan ───────────────────────────────────────────────────────────

    def _refresh_remote_procs(self):
        host = self._get_selected_host()
        if not host or (self._proc_worker and self._proc_worker.isRunning()):
            return
        self._btn_refresh_procs.setEnabled(False)
        self._mem_status.setText("Загрузка процессов...")
        self._log(f"⟳ Получение процессов с {host['name']}", "#58a6ff")
        self._proc_worker = RemoteProcessListWorker(host)
        self._proc_worker.done.connect(lambda procs, h=host: self._on_procs_loaded(procs, h))
        self._proc_worker.error.connect(lambda e: (
            self._mem_status.setText(f"✘ {e}"),
            self._log(f"✘ Ошибка процессов: {e}", "#f85149")))
        self._proc_worker.finished.connect(lambda: self._btn_refresh_procs.setEnabled(True))
        self._proc_worker.start()

    def _on_procs_loaded(self, procs: list, host: dict = None):
        self._remote_procs = procs
        self._render_remote_procs(procs)
        msg = f"Загружено {len(procs)} процессов"
        self._mem_status.setText(msg)
        self._log(f"✓ {msg}", "#3fb950")
        if host:
            host_label = f"{host['name']} ({host['ip']})"
            DashboardTab.log_processes(host_label, procs)

    def _render_remote_procs(self, procs: list):
        self._mem_proc_tbl.setRowCount(0)
        for p in procs:
            row = self._mem_proc_tbl.rowCount()
            self._mem_proc_tbl.insertRow(row)
            for col, txt in enumerate([str(p.get("pid","")), p.get("name",""), p.get("exe","")]):
                item = QTableWidgetItem(txt); item.setFont(QFont("Consolas", 10))
                self._mem_proc_tbl.setItem(row, col, item)
        self._mem_proc_count.setText(f"{len(procs)} процессов")

    def _filter_remote_procs(self, text: str):
        lo = text.lower()
        self._render_remote_procs(
            [p for p in self._remote_procs if not lo or lo in p.get("name","").lower()])

    # ── VirusTotal check for remote HASH results ──────────────────────────────

    def _start_vt_check(self):
        if not self._hash_row_map or (self._vt_worker and self._vt_worker.isRunning()):
            return
        hashes = [{"sha256": sha256, "file": fpath}
                  for fpath, (_, sha256) in self._hash_row_map.items()]
        self._btn_vt.setEnabled(False)
        self._lbl_result_stats.setText(f"VirusTotal: 0/{len(hashes)}...")
        self._vt_worker = RemoteHashVTWorker(hashes)
        self._vt_worker.progress.connect(self._lbl_result_stats.setText)
        self._vt_worker.file_done.connect(self._on_vt_file_done)
        self._vt_worker.all_done.connect(self._on_vt_all_done)
        self._vt_worker.start()

    def _on_vt_file_done(self, data: dict):
        fpath = data.get("file", "")
        if fpath not in self._hash_row_map:
            return
        row, _ = self._hash_row_map[fpath]
        status = data.get("status", "ERROR")
        mal    = data.get("mal", 0)
        total  = data.get("total", 0)
        vt_colors = {
            "MALICIOUS":  "#f85149",
            "SUSPICIOUS": "#d29922",
            "CLEAN":      "#3fb950",
            "NOT_FOUND":  "#6e7681",
            "RATE_LIMIT": "#8b949e",
        }
        color = vt_colors.get(status, "#8b949e")
        label = status + (f" {mal}/{total}" if total else "")
        item  = QTableWidgetItem(label)
        item.setFont(QFont("Consolas", 11))
        item.setForeground(QColor(color))
        self._tbl.setItem(row, 1, item)
        self._vt_results.append({
            "sha256": data.get("sha256", ""),
            "file": fpath, "status": status,
            "mal": data.get("mal", 0), "total": data.get("total", 0),
        })
        if status == "MALICIOUS":
            host_label = getattr(self, "_last_scan_host_label", "")
            DashboardTab.log_event("HASH",
                f"VT MALICIOUS: {fpath} ({mal}/{total})",
                level="high", severity="Critical",
                scan=True, host=host_label)

    def _on_vt_all_done(self):
        self._btn_vt.setEnabled(True)
        n = len(self._hash_row_map)
        self._lbl_result_stats.setText(f"VT завершён — {n} файлов проверено")
        host_label = getattr(self, "_last_scan_host_label", "")
        if self._vt_results:
            DashboardTab.log_vt_results(host_label, list(self._vt_results))
            self._vt_results = []

    def _export_hashes_csv(self):
        if not self._hash_row_map:
            return
        host_label = getattr(self, "_last_scan_host_label", "host")
        default_name = f"hashes_{host_label.replace(' ', '_').replace('(', '').replace(')', '')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV", default_name, "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Файл", "SHA256", "VT Результат"])
                for fpath, (row, sha256) in self._hash_row_map.items():
                    vt_item = self._tbl.item(row, 1)
                    vt_result = vt_item.text() if vt_item else ""
                    writer.writerow([fpath, sha256, vt_result])
            self._lbl_result_stats.setText(f"CSV сохранён: {path}")
        except Exception as e:
            self._lbl_result_stats.setText(f"✘ Ошибка экспорта: {e}")

    def _start_mem_scan(self):
        host = self._get_selected_host()
        if not host:
            return
        if self._mem_worker and self._mem_worker.isRunning():
            self._mem_status.setText("⚠ Уже выполняется"); return
        rules = self._get_selected_rules(self._mem_rule_list, self._mem_custom_rules)
        if not rules:
            self._mem_status.setText("⚠ Выберите хотя бы одно YARA правило"); return

        host_label = f"{host['name']} ({host['ip']})"
        self._mem_stop_requested = False
        self._mem_scan_host_label = host_label
        self._log(f"▶ Memory Scan на <b>{host_label}</b>  | правил: {len(rules)}", "#a371f7")
        DashboardTab.log_event("MEMORY",
            f"Memory Scan запущен — {host_label} | {len(rules)} правил",
            level="info", scan=True, host=host_label)

        self._btn_mem_scan.setEnabled(False); self._btn_mem_stop.setEnabled(True)
        self._host_list.setEnabled(False)
        self._mem_prog.setVisible(True)
        self._tbl.setRowCount(0); self._lbl_result_stats.setText("Memory Scan...")
        self._mem_status.setText(f"Сканирование памяти на {host['ip']}...")
        self._sub_tabs.setTabText(self._TAB_RESULTS, "📋  Результаты (сканирование...)")

        self._mem_worker = RemoteMemScanWorker(host, rules)
        self._mem_worker.progress.connect(lambda m: (
            self._mem_status.setText(m), self._log(f"  {m}", "#6e7681")))
        self._mem_worker.done.connect(
            lambda r: None if self._mem_stop_requested else self._on_results_done(r, host))
        self._mem_worker.error.connect(lambda m, hl=host_label: (
            self._mem_status.setText(f"✘ {m}"),
            self._log(f"✘ Memory Scan ошибка: {m}", "#f85149"),
            DashboardTab.log_event("MEMORY",
                f"✘ Ошибка Memory Scan — {hl}: {m[:80]}",
                level="high", scan=True, host=hl)))
        self._mem_worker.finished.connect(self._on_mem_finished)
        self._mem_worker.start()

    def _on_mem_finished(self):
        self._btn_mem_scan.setEnabled(True); self._btn_mem_stop.setEnabled(False)
        self._host_list.setEnabled(True)
        self._mem_prog.setVisible(False)
        self._sub_tabs.setTabText(self._TAB_RESULTS, "📋  Результаты")

    def _stop_mem_scan(self):
        self._mem_stop_requested = True
        if self._mem_worker:
            self._mem_worker.stop()
        self._btn_mem_stop.setEnabled(False)
        self._mem_status.setText("Остановлено")
        self._log("⏹ Memory Scan остановлен", "#d29922")
        hl = getattr(self, "_mem_scan_host_label", "")
        if hl:
            DashboardTab.log_event("MEMORY", f"⏹ Memory Scan остановлен — {hl}",
                level="info", scan=True, host=hl)

    # ── Shared results handler ────────────────────────────────────────────────

    def _on_results_done(self, results: list, host: dict):
        try:
            ts         = datetime.now().strftime("%H:%M:%S")
            host_label = f"{host['name']} ({host['ip']})"

            update_host(host["id"], last_scan=ts)
            for i in range(self._host_list.count()):
                item = self._host_list.item(i)
                h    = item.data(Qt.ItemDataRole.UserRole)
                if h.get("id") == host.get("id"):
                    h["last_scan"] = ts
                    item.setData(Qt.ItemDataRole.UserRole, h)
                    if h["id"] == self._selected_id:
                        self._st_scan.setText(ts)
                    break

            colors     = {"YARA":"#58a6ff","IOC":"#d29922","HASH":"#8b949e","MEMORY":"#a371f7"}
            sev_colors = {"critical":"#f85149","high":"#d29922","medium":"#58a6ff","low":"#3fb950"}

            self._tbl.setRowCount(len(results))
            self._hash_row_map = {}
            self._last_scan_host_label = host_label
            real_hits = 0
            for i, r in enumerate(results):
                typ  = r.get("type","?"); rule = r.get("rule","?")
                proc = r.get("process_name","")
                fil  = f"[{proc}] {r.get('file','?')}" if proc else r.get("file","?")
                sev  = r.get("severity","")
                col  = sev_colors.get(sev.lower(),"") or colors.get(typ,"#8b949e")
                ri   = QTableWidgetItem(f"[{typ}]  {rule}")
                si   = QTableWidgetItem(sev.upper() if sev else typ)
                fi   = QTableWidgetItem(fil)
                ri.setForeground(QColor(col)); si.setForeground(QColor(col))
                for it in (ri, si, fi):
                    it.setFont(QFont("Consolas", 11))
                self._tbl.setItem(i, 0, ri); self._tbl.setItem(i, 1, si); self._tbl.setItem(i, 2, fi)
                DashboardTab.log_event(typ, f"{rule} — {fil}",
                    level="high" if sev.lower() in ("critical","high") else "info",
                    severity=sev.capitalize() if sev else typ,
                    scan=True, target=fil[:60], host=host_label)
                if typ != "HASH" and rule not in ("ERROR","TIMEOUT","WARN","INFO","COMPILE_ERR","DEBUG"):
                    real_hits += 1
                if typ == "HASH" and r.get("sha256"):
                    self._hash_row_map[r.get("file","?")] = (i, r["sha256"])

            n_hashes = len(self._hash_row_map)
            self._btn_vt.setText(f"🔍  VT ({n_hashes})")
            self._btn_vt.setVisible(n_hashes > 0)
            self._btn_vt.setEnabled(True)
            self._btn_export_csv.setVisible(n_hashes > 0)

            yara_hits = sum(1 for r in results
                if r.get("type") in ("YARA","MEMORY")
                and r.get("rule","") not in ("ERROR","TIMEOUT","WARN","INFO","COMPILE_ERR"))
            sus_procs = sum(1 for r in results
                if r.get("type") == "IOC" and r.get("rule") == "Подозрит. процесс")
            DashboardTab.stats["yara_hits"]        += yara_hits
            DashboardTab.stats["suspicious_procs"] += sus_procs

            # Always log a summary — this ensures Remote tab always shows scan activity
            types_found = {r.get("type","") for r in results
                           if r.get("type","") not in
                           ("ERROR","WARN","TIMEOUT","COMPILE_ERR","INFO","DEBUG")}
            ev_type = ("MEMORY" if "MEMORY" in types_found
                       else "YARA" if types_found else "SCAN")
            DashboardTab.log_event(
                ev_type,
                f"{'⚠ ' + str(real_hits) + ' совп.' if real_hits else '✓ Угрозы не найдены'}"
                f" — {host_label} ({len(results)} записей)",
                level="high" if real_hits else "ok",
                scan=True, host=host_label,
            )

            if real_hits:
                msg_color = "#d29922"
                msg = f"✓ Найдено: {real_hits} совпадений (всего: {len(results)})"
            elif results:
                msg_color = "#8b949e"
                msg = f"✓ Завершено — чисто ({len(results)} записей)"
            else:
                msg_color = "#3fb950"
                msg = "✓ Завершено — угрозы не обнаружены"

            self._file_status.setText(msg)
            self._mem_status.setText(msg)
            self._lbl_result_stats.setText(
                f"{real_hits} совпадений · {len(results)} записей · {host_label}")
            self._log(msg, msg_color)
            self._sub_tabs.setCurrentIndex(self._TAB_RESULTS)

        except Exception as e:
            self._log(f"✘ Ошибка обработки результатов: {e}", "#f85149")

    # ── Deploy ────────────────────────────────────────────────────────────────

    def _deploy(self):
        dlg = _DeployDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        d = dlg.data()
        if not d["ip"] or not d["username"]:
            QMessageBox.warning(self, t("error"), t("hosts_deploy_required")); return
        if self._deploy_worker and self._deploy_worker.isRunning():
            return
        self._btn_deploy.setEnabled(False)
        self._log(f"⚙ Деплой агента на {d['ip']}...", "#58a6ff")
        self._deploy_worker = DeployWorker(d["ip"], d["username"], d["password"])
        self._deploy_worker.progress.connect(lambda m: self._log(f"  {m}", "#6e7681"))
        self._deploy_worker.error.connect(self._on_deploy_error)
        self._deploy_worker.done.connect(lambda tok: self._on_deploy_done(d["ip"], tok))
        self._deploy_worker.finished.connect(lambda: self._btn_deploy.setEnabled(True))
        self._deploy_worker.start()

    def _on_deploy_done(self, ip: str, token: str):
        self._log(f"✓ Агент задеплоен на {ip}. Токен: {token[:16]}...", "#3fb950")
        QMessageBox.information(self, t("hosts_deploy_done_title"),
            t("hosts_deploy_done_msg", ip=ip, tok=token))

    def _on_deploy_error(self, msg: str):
        self._log(f"✘ Deploy ошибка: {msg}", "#f85149")
        QMessageBox.warning(self, t("hosts_deploy_error_title"), msg)

    # ── Network Isolation ─────────────────────────────────────────────────────

    def _check_isolation_status(self):
        if self._iso_worker and self._iso_worker.isRunning():
            return
        host = self._get_selected_host()
        if not host:
            return
        self._iso_status_text.setText(t("hosts_iso_checking"))
        self._iso_status_icon.setStyleSheet("font-size:24px;color:#6e7681;")
        self._iso_worker = NetworkIsolationWorker(host, "status")
        self._iso_worker.done.connect(self._on_iso_status)
        self._iso_worker.error.connect(lambda e: (
            self._iso_status_text.setText(t("hosts_iso_err", e=str(e)[:60])),
            self._iso_log_append(t("hosts_iso_err", e=str(e)), "#f85149")))
        self._iso_worker.start()

    def _on_iso_status(self, data: dict):
        isolated = data.get("isolated", False)
        if isolated:
            self._iso_status_icon.setStyleSheet("font-size:24px;color:#f85149;")
            self._iso_status_text.setText(
                f"<span style='color:#f85149;font-weight:bold'>{t('hosts_iso_isolated')}</span>")
            self._iso_status_card.setStyleSheet(
                "QFrame{background:#2d0f0f;border:1px solid #6e1212;border-radius:8px;}")
        else:
            self._iso_status_icon.setStyleSheet("font-size:24px;color:#3fb950;")
            self._iso_status_text.setText(
                f"<span style='color:#3fb950;font-weight:bold'>{t('hosts_iso_connected')}</span>")
            self._iso_status_card.setStyleSheet(
                "QFrame{background:#0f2d14;border:1px solid #1a6e2c;border-radius:8px;}")

    def _isolate_host(self):
        if self._iso_worker and self._iso_worker.isRunning():
            return
        host = self._get_selected_host()
        if not host:
            return
        mgmt_ip = self._iso_mgmt_ip.text().strip()
        if not mgmt_ip:
            QMessageBox.warning(self, t("hosts_iso_no_mgmt_title"),
                t("hosts_iso_no_mgmt_msg")); return
        if QMessageBox.question(
            self, t("hosts_iso_confirm_title"),
            t("hosts_iso_confirm_msg", name=host['name'], ip=host['ip'], mgmt=mgmt_ip),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        self._btn_isolate.setEnabled(False)
        self._iso_status_text.setText(t("hosts_iso_applying"))
        self._iso_log_append(
            f"▶ Изоляция {host['name']} ({host['ip']}) | mgmt: {mgmt_ip}", "#d29922")
        DashboardTab.log_event("ISOLATE", f"Изоляция {host['name']} ({host['ip']})",
            level="critical", severity="Critical", host=f"{host['name']} ({host['ip']})")

        self._iso_worker = NetworkIsolationWorker(host, "isolate", mgmt_ip)
        self._iso_worker.done.connect(self._on_isolate_done)
        self._iso_worker.error.connect(lambda e: (
            self._iso_log_append(f"✘ Ошибка изоляции: {e}", "#f85149"),
            self._btn_isolate.setEnabled(True)))
        self._iso_worker.start()

    def _on_isolate_done(self, data: dict):
        self._btn_isolate.setEnabled(True)
        if data.get("status") == "isolated":
            self._iso_log_append("✓ Хост изолирован от сети", "#d29922")
            self._on_iso_status({"isolated": True})
        else:
            errs = "; ".join(data.get("errors", [str(data)]))
            self._iso_log_append(f"✘ Ошибка: {errs}", "#f85149")

    def _restore_host(self):
        if self._iso_worker and self._iso_worker.isRunning():
            return
        host = self._get_selected_host()
        if not host:
            return
        self._btn_restore.setEnabled(False)
        self._iso_log_append(
            f"▶ Восстановление сети {host['name']} ({host['ip']})", "#58a6ff")
        DashboardTab.log_event("RESTORE", f"Восстановление сети {host['name']}",
            level="info", host=f"{host['name']} ({host['ip']})")

        self._iso_worker = NetworkIsolationWorker(host, "restore")
        self._iso_worker.done.connect(self._on_restore_done)
        self._iso_worker.error.connect(lambda e: (
            self._iso_log_append(f"✘ Ошибка: {e}", "#f85149"),
            self._btn_restore.setEnabled(True)))
        self._iso_worker.start()

    def _on_restore_done(self, data: dict):
        self._btn_restore.setEnabled(True)
        if data.get("status") == "restored":
            self._iso_log_append("✓ Сеть восстановлена", "#3fb950")
            self._on_iso_status({"isolated": False})
        else:
            self._iso_log_append(f"✘ Ошибка: {data}", "#f85149")

    def closeEvent(self, event):
        for attr in ("_ping_worker", "_scan_worker", "_deploy_worker",
                     "_proc_worker", "_mem_worker", "_iso_worker", "_vt_worker"):
            w = getattr(self, attr, None)
            if w and w.isRunning():
                if hasattr(w, "stop"):
                    w.stop()
                w.quit()
                w.wait(2000)
        super().closeEvent(event)

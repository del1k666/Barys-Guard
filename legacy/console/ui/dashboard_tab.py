import datetime
import threading
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QFrame, QLabel, QTabWidget, QPushButton, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor
from core.i18n import t


_dashboard_lock = threading.Lock()


class DashboardTab(QWidget):
    stats = {
        "hash_lookups": 0, "malicious": 0, "clean": 0,
        "ioc_runs": 0, "suspicious_procs": 0,
        "yara_scans": 0, "yara_hits": 0,
        "net_checks": 0, "high_risk_ips": 0,
        "recent": [],
    }
    remote_proc_snapshots: dict = {}   # host_label -> {ts, procs}
    remote_hash_vt_results: list = []  # {host, file, sha256, status, mal, total}

    def __init__(self):
        super().__init__()
        self._build()
        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(3000)

    # ── Layout ────────────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        # Stat cards row
        cards = QHBoxLayout()
        cards.setSpacing(8)
        self._c_hash     = self._stat_card("Hash Lookups",     "0", "#58a6ff")
        self._c_mal      = self._stat_card("Malicious",        "0", "#f85149")
        self._c_yara     = self._stat_card("YARA Hits",        "0", "#d29922")
        self._c_net      = self._stat_card("Net Checks",       "0", "#3fb950")
        self._c_susp     = self._stat_card("Suspicious Procs", "0", "#d29922")
        self._c_highrisk = self._stat_card("High Risk IPs",    "0", "#f85149")
        for card in [self._c_hash, self._c_mal, self._c_yara,
                     self._c_net, self._c_susp, self._c_highrisk]:
            cards.addWidget(card[0])
        lay.addLayout(cards)

        # Filter bar
        lay.addWidget(self._build_filter_bar())

        # Sub-tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabBar::tab{padding:8px 20px;font-size:12px;}"
            "QTabBar::tab:selected{font-weight:bold;}"
        )
        self._tabs.addTab(self._build_overview_tab(),  t("dash_tab_overview"))
        self._tabs.addTab(self._build_events_tab(),    t("dash_tab_events"))
        self._tabs.addTab(self._build_scans_tab(),     t("dash_tab_scans"))
        self._tabs.addTab(self._build_remote_tab(),    t("dash_tab_remote"))
        self._tabs.addTab(self._build_procs_tab(),     t("dash_tab_procs"))
        lay.addWidget(self._tabs)

    def _build_filter_bar(self) -> QWidget:
        w = QWidget()
        fl = QHBoxLayout(w)
        fl.setContentsMargins(2, 2, 2, 2)
        fl.setSpacing(8)

        _lbl_style = "color:#8b949e;font-size:11px;"
        _cb_style = (
            "QComboBox{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
            "border-radius:4px;padding:2px 8px;font-size:11px;}"
            "QComboBox::drop-down{border:none;}"
        )

        self._lbl_period = QLabel(t("dash_period_lbl"))
        self._lbl_period.setStyleSheet(_lbl_style)
        fl.addWidget(self._lbl_period)

        self._combo_time = QComboBox()
        self._combo_time.addItems([
            t("dash_time_all"), t("dash_time_hour"),
            t("dash_time_today"), t("dash_time_24h"),
        ])
        self._combo_time.setFixedWidth(150)
        self._combo_time.setStyleSheet(_cb_style)
        self._combo_time.currentIndexChanged.connect(self._refresh)
        fl.addWidget(self._combo_time)

        fl.addSpacing(16)
        self._lbl_host_filter = QLabel(t("dash_host_lbl"))
        self._lbl_host_filter.setStyleSheet(_lbl_style)
        fl.addWidget(self._lbl_host_filter)

        self._combo_host = QComboBox()
        self._combo_host.setFixedWidth(230)
        self._combo_host.setStyleSheet(_cb_style)
        self._combo_host.addItem(t("dash_all_hosts"))
        self._combo_host.currentIndexChanged.connect(self._refresh)
        fl.addWidget(self._combo_host)

        fl.addStretch()
        return w

    # ── Tab: Overview ─────────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        w   = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        self._grp_sev = QGroupBox(t("dash_threats_grp"))
        grp_sev = self._grp_sev
        gs = QVBoxLayout(grp_sev)
        gs.setSpacing(12)
        self._bars = {}
        for label, color in [
            ("Critical", "#f85149"), ("High",   "#d29922"),
            ("Medium",   "#58a6ff"), ("Low",    "#3fb950"),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(65)
            lbl.setStyleSheet(
                f"color:{color};font-size:12px;font-weight:bold;")
            bar = QProgressBar()
            bar.setRange(0, 100); bar.setValue(0)
            bar.setFixedHeight(16)
            bar.setStyleSheet(
                f"QProgressBar{{background:#21262d;border:none;border-radius:4px;}}"
                f"QProgressBar::chunk{{background:{color};border-radius:4px;}}")
            cnt = QLabel("0")
            cnt.setFixedWidth(35)
            cnt.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            cnt.setStyleSheet("color:#6e7681;font-size:12px;font-weight:bold;")
            row.addWidget(lbl); row.addWidget(bar); row.addWidget(cnt)
            gs.addLayout(row)
            self._bars[label] = (bar, cnt)
        gs.addStretch()

        self._grp_quick = QGroupBox(t("dash_recent_grp"))
        grp_quick = self._grp_quick
        gq = QVBoxLayout(grp_quick)
        self._quick_log = QTextEdit()
        self._quick_log.setReadOnly(True)
        self._quick_log.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:6px;"
            "font-family:Consolas,monospace;font-size:12px;padding:8px;"
        )
        gq.addWidget(self._quick_log)

        lay.addWidget(grp_sev,   1)
        lay.addWidget(grp_quick, 2)
        return w

    # ── Tab: Events ───────────────────────────────────────────────────────

    def _build_events_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self._lbl_event_count = QLabel("0 событий")
        self._lbl_event_count.setStyleSheet("color:#6e7681;font-size:11px;")
        btn_clear = QPushButton(t("dash_clear_btn"))
        btn_clear.setObjectName("secondaryBtn")
        btn_clear.setFixedHeight(26)
        btn_clear.clicked.connect(self._clear_events)
        self._btn_clear_events = btn_clear
        top.addWidget(self._lbl_event_count)
        top.addStretch()
        top.addWidget(btn_clear)
        lay.addLayout(top)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "background:#0a0e14;border:1px solid #21262d;border-radius:6px;"
            "font-family:Consolas,monospace;font-size:12px;padding:8px;"
        )
        lay.addWidget(self.log)
        return w

    # ── Tab: Local scans ──────────────────────────────────────────────────

    def _build_scans_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        self._lbl_scans_count = QLabel("0 записей")
        self._lbl_scans_count.setStyleSheet("color:#6e7681;font-size:11px;")
        lay.addWidget(self._lbl_scans_count)

        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels([
            t("dash_tbl_time"), t("dash_tbl_type"),
            t("dash_tbl_target"), t("dash_tbl_result"),
        ])
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl.horizontalHeader().resizeSection(0, 75)
        self.tbl.horizontalHeader().resizeSection(1, 60)
        self.tbl.horizontalHeader().resizeSection(2, 200)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setAlternatingRowColors(True)
        self.tbl.setStyleSheet("QTableWidget{alternate-background-color:#0f1318;}")
        lay.addWidget(self.tbl)
        return w

    # ── Tab: Remote scans ─────────────────────────────────────────────────

    def _build_remote_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        self._lbl_remote_count = QLabel("0 записей")
        self._lbl_remote_count.setStyleSheet("color:#6e7681;font-size:11px;")
        lay.addWidget(self._lbl_remote_count)

        self.remote_tbl = QTableWidget(0, 4)
        self.remote_tbl.setHorizontalHeaderLabels([
            t("dash_tbl_time"), t("dash_tbl_host"),
            t("dash_tbl_type"), t("dash_tbl_rule"),
        ])
        self.remote_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.remote_tbl.horizontalHeader().resizeSection(0, 85)
        self.remote_tbl.horizontalHeader().resizeSection(1, 190)
        self.remote_tbl.horizontalHeader().resizeSection(2, 70)
        self.remote_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.remote_tbl.setAlternatingRowColors(True)
        self.remote_tbl.setStyleSheet(
            "QTableWidget{alternate-background-color:#0f1318;}")
        lay.addWidget(self.remote_tbl)
        return w

    # ── Tab: Remote processes ─────────────────────────────────────────────

    def _build_procs_tab(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self._lbl_procs_count = QLabel("0 процессов")
        self._lbl_procs_count.setStyleSheet("color:#6e7681;font-size:11px;")
        btn_procs_csv = QPushButton("💾  CSV")
        btn_procs_csv.setObjectName("secondaryBtn")
        btn_procs_csv.setFixedHeight(26)
        btn_procs_csv.clicked.connect(self._export_procs_csv)
        top.addWidget(self._lbl_procs_count)
        top.addStretch()
        top.addWidget(btn_procs_csv)
        lay.addLayout(top)

        self._procs_tbl = QTableWidget(0, 4)
        self._procs_tbl.setHorizontalHeaderLabels([
            t("dash_tbl_host"), t("dash_tbl_pid"),
            t("dash_tbl_proc_name"), t("dash_tbl_proc_path"),
        ])
        self._procs_tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._procs_tbl.horizontalHeader().resizeSection(0, 160)
        self._procs_tbl.horizontalHeader().resizeSection(1, 55)
        self._procs_tbl.horizontalHeader().resizeSection(2, 170)
        self._procs_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._procs_tbl.setAlternatingRowColors(True)
        self._procs_tbl.setStyleSheet(
            "QTableWidget{alternate-background-color:#0f1318;}")
        lay.addWidget(self._procs_tbl)
        return w

    # ── Stat card ─────────────────────────────────────────────────────────

    def _stat_card(self, title, value, color):
        f = QFrame()
        f.setStyleSheet(
            "QFrame{background:#161b22;border:1px solid #21262d;border-radius:8px;}")
        fl = QVBoxLayout(f)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(
            "color:#6e7681;font-size:10px;font-weight:bold;letter-spacing:1px;")
        t.setWordWrap(True)
        v = QLabel(value)
        v.setStyleSheet(f"font-size:28px;font-weight:bold;color:{color};")
        fl.addWidget(t); fl.addWidget(v)
        return f, v

    # ── Filtering ─────────────────────────────────────────────────────────

    def _get_filtered(self, events: list) -> list:
        now = time.time()
        time_idx = self._combo_time.currentIndex()
        host_idx  = self._combo_host.currentIndex()
        host_sel  = self._combo_host.currentText()

        result = []
        for evt in events:
            ts = evt.get("ts", now)
            if time_idx == 1 and now - ts > 3600:
                continue
            if time_idx == 2:
                evt_date = datetime.datetime.fromtimestamp(ts).date()
                if evt_date != datetime.datetime.now().date():
                    continue
            if time_idx == 3 and now - ts > 86400:
                continue

            if host_idx == 0:
                pass  # all hosts
            elif host_idx == 1:
                if evt.get("host"):
                    continue
            else:
                if evt.get("host", "") != host_sel:
                    continue

            result.append(evt)
        return result

    def _update_host_combo(self, recent: list):
        seen = sorted(
            {evt["host"] for evt in recent if evt.get("host")} |
            set(DashboardTab.remote_proc_snapshots.keys())
        )
        cur_idx = self._combo_host.currentIndex()

        new_items = [t("dash_all_hosts"), t("dash_local")] + seen
        old_items = [self._combo_host.itemText(i)
                     for i in range(self._combo_host.count())]
        if new_items == old_items:
            return

        self._combo_host.blockSignals(True)
        self._combo_host.clear()
        for item in new_items:
            self._combo_host.addItem(item)
        self._combo_host.setCurrentIndex(max(0, min(cur_idx, len(new_items) - 1)))
        self._combo_host.blockSignals(False)

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refresh(self):
        s        = DashboardTab.stats
        recent   = s["recent"]
        now      = time.time()
        time_idx = self._combo_time.currentIndex()

        self._update_host_combo(recent)
        filtered = self._get_filtered(recent)

        col_map = {"critical": "#f85149", "high": "#d29922",
                   "info": "#8b949e", "ok": "#3fb950"}

        # Stat cards (always global, not filtered)
        self._c_hash[1].setText(str(s["hash_lookups"]))
        self._c_mal[1].setText(str(s["malicious"]))
        self._c_yara[1].setText(str(s["yara_hits"]))
        self._c_net[1].setText(str(s["net_checks"]))
        self._c_susp[1].setText(str(s["suspicious_procs"]))
        self._c_highrisk[1].setText(str(s["high_risk_ips"]))

        # ── Tab 1: Overview — quick log (last 10 from filtered) ───────
        self._quick_log.clear()
        for evt in reversed(filtered[-10:]):
            col = col_map.get(evt.get("level", "info"), "#8b949e")
            self._quick_log.append(
                f'<span style="color:#484f58">{evt.get("time","")}</span> '
                f'<span style="color:{col};font-weight:bold">[{evt.get("type","")}]</span> '
                f'<span style="color:#c9d1d9">{evt.get("msg","")}</span>'
            )

        counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
        for evt in filtered:
            sev = evt.get("severity", "")
            if sev in counts:
                counts[sev] += 1
        total = max(sum(counts.values()), 1)
        for label, (bar, cnt) in self._bars.items():
            n = counts[label]
            bar.setValue(int(n / total * 100))
            cnt.setText(str(n))

        # ── Tab 2: Events — full filtered log ─────────────────────────
        n_events = len(filtered)
        self._lbl_event_count.setText(t("dash_events_count", n=n_events))
        self.log.clear()
        for evt in reversed(filtered[-100:]):
            col = col_map.get(evt.get("level", "info"), "#8b949e")
            self.log.append(
                f'<span style="color:#484f58">{evt.get("time","")}</span> '
                f'<span style="color:{col};font-weight:bold">[{evt.get("type","")}]</span> '
                f'<span style="color:#c9d1d9">{evt.get("msg","")}</span>'
            )

        # ── Tab 3: Local scans ────────────────────────────────────────
        scans = [e for e in filtered if e.get("scan") and not e.get("host")]
        n_scans = len(scans)
        self._lbl_scans_count.setText(t("dash_records_count", n=n_scans))
        selected_scans = self.tbl.currentRow()
        self.tbl.setRowCount(0)
        for evt in reversed(scans[-50:]):
            row = self.tbl.rowCount()
            self.tbl.insertRow(row)
            col = col_map.get(evt.get("level", "info"), "#8b949e")
            for i, txt in enumerate([
                evt.get("time", ""), evt.get("type", ""),
                evt.get("target", ""), evt.get("msg", ""),
            ]):
                item = QTableWidgetItem(txt)
                item.setFont(QFont("Consolas", 11))
                if i == 3:
                    item.setForeground(QColor(col))
                self.tbl.setItem(row, i, item)
        if 0 <= selected_scans < self.tbl.rowCount():
            self.tbl.setCurrentCell(selected_scans, 0)

        # ── Tab 4: Remote scans ───────────────────────────────────────
        remote = [e for e in filtered if e.get("host")]
        n_remote = len(remote)
        self._lbl_remote_count.setText(t("dash_records_count", n=n_remote))
        selected_remote = self.remote_tbl.currentRow()
        self.remote_tbl.setRowCount(0)
        rem_col = {"YARA": "#58a6ff", "IOC": "#d29922",
                   "MEMORY": "#a371f7", "HASH": "#8b949e"}
        for evt in reversed(remote[-100:]):
            row = self.remote_tbl.rowCount()
            self.remote_tbl.insertRow(row)
            typ = evt.get("type", "")
            col = rem_col.get(typ, "#8b949e")
            for i, txt in enumerate([
                evt.get("time", ""), evt.get("host", ""),
                typ, evt.get("msg", ""),
            ]):
                item = QTableWidgetItem(txt)
                item.setFont(QFont("Consolas", 11))
                if i in (2, 3):
                    item.setForeground(QColor(col))
                self.remote_tbl.setItem(row, i, item)
        if 0 <= selected_remote < self.remote_tbl.rowCount():
            self.remote_tbl.setCurrentCell(selected_remote, 0)

        # ── Tab 5: Remote process snapshots ──────────────────────────────
        host_sel  = self._combo_host.currentText()
        snapshots = DashboardTab.remote_proc_snapshots
        if host_sel == "Локальный":
            snap_hosts = {}
        elif host_sel != "Все хосты" and host_sel in snapshots:
            snap_hosts = {host_sel: snapshots[host_sel]}
        else:
            snap_hosts = snapshots

        selected_procs = self._procs_tbl.currentRow()
        self._procs_tbl.setRowCount(0)
        for hlabel, snap in snap_hosts.items():
            snap_ts    = snap.get("ts", now) if isinstance(snap, dict) else now
            snap_procs = snap.get("procs", snap) if isinstance(snap, dict) else snap
            if time_idx == 1 and now - snap_ts > 3600:
                continue
            if time_idx == 2:
                if datetime.datetime.fromtimestamp(snap_ts).date() != datetime.datetime.now().date():
                    continue
            if time_idx == 3 and now - snap_ts > 86400:
                continue
            for p in snap_procs:
                row = self._procs_tbl.rowCount()
                self._procs_tbl.insertRow(row)
                for i, txt in enumerate([
                    hlabel, str(p.get("pid", "")),
                    p.get("name", ""), p.get("exe", ""),
                ]):
                    item = QTableWidgetItem(txt)
                    item.setFont(QFont("Consolas", 11))
                    self._procs_tbl.setItem(row, i, item)
        n_procs = self._procs_tbl.rowCount()
        if 0 <= selected_procs < n_procs:
            self._procs_tbl.setCurrentCell(selected_procs, 0)
        self._lbl_procs_count.setText(t("dash_procs_count", n=n_procs))

        # Tab badges
        base_e = t("dash_tab_events")
        base_s = t("dash_tab_scans")
        base_r = t("dash_tab_remote")
        base_p = t("dash_tab_procs")
        self._tabs.setTabText(1, f"{base_e} ({n_events})" if n_events else base_e)
        self._tabs.setTabText(2, f"{base_s} ({n_scans})"  if n_scans  else base_s)
        self._tabs.setTabText(3, f"{base_r} ({n_remote})" if n_remote else base_r)
        self._tabs.setTabText(4, f"{base_p} ({n_procs})"  if n_procs  else base_p)

    def _clear_events(self):
        DashboardTab.stats["recent"] = []
        DashboardTab.remote_proc_snapshots.clear()
        DashboardTab.remote_hash_vt_results.clear()
        self.log.clear()
        self._quick_log.clear()
        self._procs_tbl.setRowCount(0)
        self._lbl_procs_count.setText(t("dash_procs_count", n=0))
        self._lbl_event_count.setText(t("dash_events_count", n=0))
        for label, (bar, cnt) in self._bars.items():
            bar.setValue(0); cnt.setText("0")
        self._tabs.setTabText(1, t("dash_tab_events"))
        self._tabs.setTabText(2, t("dash_tab_scans"))
        self._tabs.setTabText(3, t("dash_tab_remote"))
        self._tabs.setTabText(4, t("dash_tab_procs"))

    def retranslate(self, _lang: str = ""):
        self._tabs.setTabText(0, t("dash_tab_overview"))
        self._tabs.setTabText(1, t("dash_tab_events"))
        self._tabs.setTabText(2, t("dash_tab_scans"))
        self._tabs.setTabText(3, t("dash_tab_remote"))
        self._tabs.setTabText(4, t("dash_tab_procs"))
        self._grp_sev.setTitle(t("dash_threats_grp"))
        self._grp_quick.setTitle(t("dash_recent_grp"))
        self._lbl_period.setText(t("dash_period_lbl"))
        self._lbl_host_filter.setText(t("dash_host_lbl"))
        idx = self._combo_time.currentIndex()
        self._combo_time.blockSignals(True)
        self._combo_time.clear()
        self._combo_time.addItems([
            t("dash_time_all"), t("dash_time_hour"),
            t("dash_time_today"), t("dash_time_24h"),
        ])
        self._combo_time.setCurrentIndex(idx)
        self._combo_time.blockSignals(False)
        self._btn_clear_events.setText(t("dash_clear_btn"))
        self.tbl.setHorizontalHeaderLabels([
            t("dash_tbl_time"), t("dash_tbl_type"),
            t("dash_tbl_target"), t("dash_tbl_result"),
        ])
        self.remote_tbl.setHorizontalHeaderLabels([
            t("dash_tbl_time"), t("dash_tbl_host"),
            t("dash_tbl_type"), t("dash_tbl_rule"),
        ])
        self._procs_tbl.setHorizontalHeaderLabels([
            t("dash_tbl_host"), t("dash_tbl_pid"),
            t("dash_tbl_proc_name"), t("dash_tbl_proc_path"),
        ])
        self._update_host_combo(DashboardTab.stats.get("recent", []))

    @staticmethod
    def log_event(type_, msg, level="info", severity="",
                  target="", scan=False, host=""):
        with _dashboard_lock:
            DashboardTab.stats["recent"].append({
                "ts":       time.time(),
                "time":     datetime.datetime.now().strftime("%H:%M:%S"),
                "type":     type_,
                "msg":      msg,
                "level":    level,
                "severity": severity,
                "target":   target,
                "scan":     scan,
                "host":     host,
            })
            if len(DashboardTab.stats["recent"]) > 500:
                DashboardTab.stats["recent"] = DashboardTab.stats["recent"][-500:]

    @staticmethod
    def log_processes(host_label: str, procs: list):
        DashboardTab.remote_proc_snapshots[host_label] = {
            "ts": time.time(), "procs": procs}

    @staticmethod
    def log_vt_results(host_label: str, results: list):
        for r in results:
            DashboardTab.remote_hash_vt_results.append({"host": host_label, **r})
        if len(DashboardTab.remote_hash_vt_results) > 500:
            DashboardTab.remote_hash_vt_results = DashboardTab.remote_hash_vt_results[-500:]

    def _export_procs_csv(self):
        import csv as _csv
        from PyQt6.QtWidgets import QFileDialog as _QFD
        path, _ = _QFD.getSaveFileName(
            self, "Сохранить процессы CSV", "processes.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.writer(f)
                w.writerow(["Хост", "PID", "Имя процесса", "Путь EXE"])
                for row in range(self._procs_tbl.rowCount()):
                    w.writerow([
                        (self._procs_tbl.item(row, c).text()
                         if self._procs_tbl.item(row, c) else "")
                        for c in range(4)
                    ])
            self._lbl_procs_count.setText(f"CSV сохранён: {path}")
        except Exception as e:
            self._lbl_procs_count.setText(f"✘ Ошибка: {e}")

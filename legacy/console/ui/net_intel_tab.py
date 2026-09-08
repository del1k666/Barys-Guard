from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from workers.net_worker import NetIntelWorker
from ui.dashboard_tab import DashboardTab


class NetIntelTab(QWidget):
    def __init__(self):
        super().__init__()
        self._history = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self); lay.setSpacing(10); lay.setContentsMargins(16,16,16,16)

        grp = QGroupBox("Проверка IP / домена")
        gl  = QHBoxLayout(grp)
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("Введите IP адрес (например 8.8.8.8)...")
        self.inp.returnPressed.connect(self._lookup)
        gl.addWidget(self.inp)
        self.btn = QPushButton("Проверить")
        self.btn.setFixedWidth(110); self.btn.clicked.connect(self._lookup)
        gl.addWidget(self.btn)
        lay.addWidget(grp)

        self.prog = QProgressBar(); self.prog.setRange(0,0)
        self.prog.setVisible(False); self.prog.setFixedHeight(4)
        lay.addWidget(self.prog)

        # Score card
        self.score_frame = QFrame()
        self.score_frame.setStyleSheet("QFrame{background:#161b22;border:1px solid #30363d;border-radius:8px;}")
        sf = QHBoxLayout(self.score_frame); sf.setContentsMargins(16,12,16,12)

        vl = QVBoxLayout()
        self.score_label = QLabel("—")
        self.score_label.setStyleSheet("font-size:48px;font-weight:bold;color:#58a6ff;")
        self.score_title = QLabel("ABUSE SCORE")
        self.score_title.setStyleSheet("color:#6e7681;font-size:10px;font-weight:bold;letter-spacing:2px;")
        vl.addWidget(self.score_title); vl.addWidget(self.score_label)
        sf.addLayout(vl)

        vl2 = QVBoxLayout(); vl2.setSpacing(4)
        self.f_country  = self._info_row(vl2, "Страна")
        self.f_isp      = self._info_row(vl2, "ISP")
        self.f_org      = self._info_row(vl2, "Организация")
        self.f_reports  = self._info_row(vl2, "Репортов")
        self.f_last     = self._info_row(vl2, "Последний репорт")
        sf.addLayout(vl2, 1)

        vl3 = QVBoxLayout(); vl3.setSpacing(4)
        self.f_city     = self._info_row(vl3, "Город")
        self.f_as       = self._info_row(vl3, "AS")
        self.f_usage    = self._info_row(vl3, "Тип использования")
        self.f_tor      = self._info_row(vl3, "Tor / VPN")
        self.f_domain   = self._info_row(vl3, "Domain")
        sf.addLayout(vl3, 1)

        lay.addWidget(self.score_frame)

        # History table
        grp2 = QGroupBox("История проверок")
        gl2  = QVBoxLayout(grp2)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["IP / Host", "Score", "Страна", "ISP", "Репортов"])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl.setFixedHeight(160)
        gl2.addWidget(self.tbl)
        lay.addWidget(grp2)

        # Raw output
        self.out = QTextEdit(); self.out.setReadOnly(True)
        self.out.setPlaceholderText("Детальный ответ API появится здесь...")
        lay.addWidget(self.out)

    def _info_row(self, layout, label):
        row = QHBoxLayout(); row.setSpacing(6)
        lbl = QLabel(label + ":"); lbl.setStyleSheet("color:#6e7681;font-size:11px;min-width:120px;")
        val = QLabel("—"); val.setStyleSheet("color:#e6edf3;font-size:12px;")
        row.addWidget(lbl); row.addWidget(val); row.addStretch()
        layout.addLayout(row)
        return val

    def _lookup(self):
        t = self.inp.text().strip()
        if not t: return
        self.btn.setEnabled(False); self.prog.setVisible(True)
        self.score_label.setText("—")
        self.out.clear()
        self._w = NetIntelWorker(t)
        self._w.result.connect(self._on_result)
        self._w.error.connect(self._on_error)
        self._w.finished.connect(lambda: (self.btn.setEnabled(True), self.prog.setVisible(False)))
        self._w.start()

    def _on_result(self, data):
        abuse = data.get("abuse") or {}
        geo   = data.get("geo")   or {}
        score = abuse.get("abuseConfidenceScore", 0)
        reports = abuse.get("totalReports", 0)

        # Score color
        if score >= 80:
            col = "#f85149"
        elif score >= 30:
            col = "#d29922"
        else:
            col = "#3fb950"
        self.score_label.setText(str(score))
        self.score_label.setStyleSheet(f"font-size:48px;font-weight:bold;color:{col};")

        self.f_country.setText(f"{abuse.get('countryCode','—')}  {geo.get('country','')}")
        self.f_isp.setText(geo.get("isp", abuse.get("isp","—")))
        self.f_org.setText(geo.get("org","—"))
        self.f_reports.setText(str(reports))
        last = abuse.get("lastReportedAt","—") or "—"
        self.f_last.setText(last[:10] if last != "—" else "—")
        self.f_city.setText(f"{geo.get('city','—')}, {geo.get('regionName','')}")
        self.f_as.setText(geo.get("as","—"))
        self.f_usage.setText(abuse.get("usageType","—") or "—")
        is_tor = abuse.get("isTor", False) or abuse.get("isVpn", False)
        self.f_tor.setText("Да" if is_tor else "Нет")
        self.f_domain.setText(abuse.get("domain","—") or "—")

        # Add to history table
        row = self.tbl.rowCount()
        self.tbl.insertRow(row)
        items = [
            data["target"], str(score),
            abuse.get("countryCode","—"),
            geo.get("isp", "—"),
            str(reports)
        ]
        colors = ["#e6edf3", col, "#e6edf3", "#e6edf3", "#e6edf3"]
        for i, (txt, c) in enumerate(zip(items, colors)):
            item = QTableWidgetItem(txt)
            item.setForeground(QColor(c))
            item.setFont(QFont("Consolas", 11))
            self.tbl.setItem(row, i, item)

        # Raw output
        lines = ["=" * 50, f"  РЕЗУЛЬТАТ: {data['target']}", "=" * 50]
        lines.append(f"  Abuse Score  : {score}/100")
        lines.append(f"  Репортов     : {reports}")
        lines.append(f"  Страна       : {abuse.get('countryCode','—')}")
        lines.append(f"  ISP          : {geo.get('isp','—')}")
        lines.append(f"  Организация  : {geo.get('org','—')}")
        lines.append(f"  Город        : {geo.get('city','—')}")
        lines.append(f"  AS           : {geo.get('as','—')}")
        lines.append(f"  Tor/VPN      : {is_tor}")
        lines.append(f"  Тип          : {abuse.get('usageType','—')}")
        lines.append(f"  Посл. репорт : {last[:10] if last != '—' else '—'}")
        if data.get("abuse_err"):
            lines.append(f"  AbuseIPDB err: {data['abuse_err']}")
        if data.get("geo_err"):
            lines.append(f"  GeoIP err    : {data['geo_err']}")
        lines.append("=" * 50)
        self.out.setText("\n".join(lines))
        DashboardTab.stats["net_checks"] += 1
        if score >= 50:
            DashboardTab.stats["high_risk_ips"] += 1
            DashboardTab.log_event("NET", f"{data['target']} — score {score}/100",
                level="critical" if score>=80 else "high",
                severity="Critical" if score>=80 else "High",
                scan=True, target=data["target"])
        else:
            DashboardTab.log_event("NET", f"{data['target']} — score {score}/100",
                level="ok", severity="Low", scan=True, target=data["target"])

    def _on_error(self, msg):
        self.out.setText(f"Ошибка: {msg}")

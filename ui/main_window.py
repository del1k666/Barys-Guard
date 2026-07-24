from pathlib import Path
from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QComboBox, QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

_ICON_PATH = str(Path(__file__).parent.parent / "docs" / "img" / "image.png")
from ui.dashboard_tab import DashboardTab
from ui.hash_tab import HashTab
from ui.ioc_tab import IOCTab
from ui.yara_tab import YARATab
from ui.report_tab import ReportTab
from ui.net_intel_tab import NetIntelTab
from ui.quarantine_tab import QuarantineTab
from ui.memory_scanner_tab import MemoryScannerTab
from ui.settings_tab import SettingsTab
from ui.hosts_tab import HostsTab
from ui.hunt_tab import HuntTab
import core.host_state as host_state
from core.hosts_config import load_hosts
from core.i18n import t
from core.lang_signal import lang_signal

_TAB_KEYS = [
    "tab_dashboard", "tab_hash", "tab_ioc", "tab_yara", "tab_net",
    "tab_report", "tab_memory", "tab_quarantine", "tab_settings", "tab_hosts",
    "tab_hunt",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(t("app_title"))
        self.setWindowIcon(QIcon(_ICON_PATH))
        self.setMinimumSize(1000, 700)
        self.resize(1150, 780)

        central = QWidget()
        self.setCentralWidget(central)
        ml = QVBoxLayout(central)
        ml.setSpacing(0)
        ml.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        hdr.setFixedHeight(64)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 20, 0)
        hl.setSpacing(0)

        logo = QLabel("BARYSGUARD")
        logo.setStyleSheet("font-size:20px;font-weight:bold;color:#58a6ff;letter-spacing:2px;")
        hl.addWidget(logo)
        hl.addStretch()

        self._subtitle = QLabel(t("app_subtitle"))
        self._subtitle.setStyleSheet("font-size:10px;color:#8b949e;letter-spacing:1px;")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._subtitle)
        hl.addSpacing(16)
        self._host_combo = QComboBox()
        self._host_combo.setFixedWidth(200)
        self._host_combo.setStyleSheet(
            "QComboBox{background:#0d1117;color:#58a6ff;border:1px solid #30363d;"
            "border-radius:4px;padding:2px 8px;font-size:12px;}"
        )
        self._host_combo.currentIndexChanged.connect(self._combo_changed)
        hl.addWidget(self._host_combo)
        self._refresh_host_combo()
        ml.addWidget(hdr)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setContentsMargins(12, 12, 12, 12)
        self.dash_tab = DashboardTab()
        self._settings_tab = SettingsTab()
        self._yara_tab     = YARATab()
        self._hosts_tab    = HostsTab(
            on_host_changed=self._on_host_changed,
            on_hosts_list_changed=self._refresh_host_combo,
        )

        self._tabs.addTab(self.dash_tab,        t("tab_dashboard"))
        self._tabs.addTab(HashTab(),             t("tab_hash"))
        self._tabs.addTab(IOCTab(),              t("tab_ioc"))
        self._tabs.addTab(self._yara_tab,        t("tab_yara"))
        self._tabs.addTab(NetIntelTab(),         t("tab_net"))
        self._report_tab = ReportTab()
        self._tabs.addTab(self._report_tab,      t("tab_report"))
        self.mem_tab = MemoryScannerTab()
        self._tabs.addTab(self.mem_tab,          t("tab_memory"))
        self.quarantine_tab = QuarantineTab()
        self._tabs.addTab(self.quarantine_tab,   t("tab_quarantine"))
        self._tabs.addTab(self._settings_tab,    t("tab_settings"))
        self._tabs.addTab(self._hosts_tab,       t("tab_hosts"))
        self._hunt_tab = HuntTab()
        self._tabs.addTab(self._hunt_tab,        t("tab_hunt"))
        ml.addWidget(self._tabs)

        # Footer
        ftr = QWidget()
        ftr.setStyleSheet("background:#161b22;border-top:1px solid #30363d;")
        ftr.setFixedHeight(26)
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(14, 0, 14, 0)
        fl.addWidget(QLabel("VirusTotal API  ·  YARA Engine  ·  AbuseIPDB"))
        fl.addStretch()
        fl.addWidget(QLabel("© 2026 BarysGuard"))
        for w in ftr.findChildren(QLabel):
            w.setStyleSheet("color:#484f58;font-size:10px;")
        ml.addWidget(ftr)

        lang_signal.changed.connect(self._on_lang_changed)

    # ── Language ─────────────────────────────────────────────────────────────

    def _on_lang_changed(self, lang: str):
        self.setWindowTitle(t("app_title"))
        self._subtitle.setText(t("app_subtitle"))
        for i, key in enumerate(_TAB_KEYS):
            self._tabs.setTabText(i, t(key))
        self._refresh_host_combo()
        # Retranslate all tabs that support it
        for tab in (self.dash_tab, self._yara_tab, self._settings_tab, self._hosts_tab,
                    self._hunt_tab, self._report_tab, self.quarantine_tab):
            if hasattr(tab, "retranslate"):
                tab.retranslate(lang)

    # ── Host combo ───────────────────────────────────────────────────────────

    def _refresh_host_combo(self):
        self._host_combo.blockSignals(True)
        self._host_combo.clear()
        self._host_combo.addItem(t("combo_local"), None)
        for h in load_hosts():
            self._host_combo.addItem(f"🌐  {h['name']}  ({h['ip']})", h)
        self._host_combo.blockSignals(False)

    def _combo_changed(self, idx: int):
        h = self._host_combo.itemData(idx)
        host_state.set_current_host(h)

    def _on_host_changed(self, host):
        host_state.set_current_host(host)
        self._refresh_host_combo()
        self._host_combo.blockSignals(True)
        for i in range(self._host_combo.count()):
            d = self._host_combo.itemData(i)
            if (host is None and d is None) or (d and host and d.get("id") == host.get("id")):
                self._host_combo.setCurrentIndex(i)
                break
        else:
            self._host_combo.setCurrentIndex(0)
            host_state.set_current_host(None)
        self._host_combo.blockSignals(False)

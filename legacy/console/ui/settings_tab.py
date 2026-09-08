import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QLineEdit,
    QPushButton, QFileDialog, QScrollArea, QMessageBox, QCheckBox
)
from PyQt6.QtCore import QTimer
from config import Config
from core.i18n import t, lang_names, current_lang, set_lang
from core.lang_signal import lang_signal


class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build()
        self._load()
        lang_signal.changed.connect(self.retranslate)

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setSpacing(14)
        lay.setContentsMargins(20, 16, 20, 16)

        # Language selector
        self.grp_lang = QGroupBox(t("settings_lang_section"))
        gl = QHBoxLayout(self.grp_lang)
        gl.setSpacing(8)
        self._lang_btns = {}
        for code, label in lang_names().items():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(72)
            btn.setFixedHeight(32)
            btn.setChecked(code == current_lang())
            btn.clicked.connect(lambda _, c=code: self._change_lang(c))
            gl.addWidget(btn)
            self._lang_btns[code] = btn
        gl.addStretch()
        lay.addWidget(self.grp_lang)

        # API keys
        self.grp_api = QGroupBox(t("settings_api_keys"))
        ga = QVBoxLayout(self.grp_api)
        ga.setSpacing(10)
        self.vt_inp    = self._key_field(ga, "VirusTotal API Key",  "vt_api_key",
            "virustotal.com/gui/my-apikey  (4 req/min free)")
        self.abuse_inp = self._key_field(ga, "AbuseIPDB API Key",   "abuseipdb_key",
            "abuseipdb.com/api  (1000 req/day free)")
        lay.addWidget(self.grp_api)

        # Folders
        self.grp_dir = QGroupBox(t("settings_folders"))
        gd = QVBoxLayout(self.grp_dir)
        gd.setSpacing(8)
        self.results_inp = self._dir_field(gd, t("settings_results_dir"), "results_dir")
        self.quar_inp    = self._dir_field(gd, t("settings_quar_dir"),    "quarantine_dir")
        lay.addWidget(self.grp_dir)

        # Misc
        self.grp_misc = QGroupBox(t("settings_misc"))
        gm = QVBoxLayout(self.grp_misc)
        gm.setSpacing(8)

        rate_row = QHBoxLayout()
        self.lbl_rate = QLabel(t("settings_rate_label"))
        rate_row.addWidget(self.lbl_rate)
        self.rate_inp = QLineEdit()
        self.rate_inp.setFixedWidth(80)
        rate_row.addWidget(self.rate_inp)
        rate_hint = QLabel("Free VT — 4/min (15 sec). Paid — can be lower.")
        rate_hint.setStyleSheet("color:#6e7681;font-size:11px;")
        rate_row.addWidget(rate_hint)
        rate_row.addStretch()
        gm.addLayout(rate_row)

        self.chk_autosave = QCheckBox(t("settings_autosave"))
        gm.addWidget(self.chk_autosave)
        lay.addWidget(self.grp_misc)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton(t("settings_save_btn"))
        self.btn_save.setFixedHeight(38)
        self.btn_save.clicked.connect(self._save_all)
        btn_row.addWidget(self.btn_save)

        self.btn_reset = QPushButton(t("settings_reset_btn"))
        self.btn_reset.setObjectName("dangerBtn")
        self.btn_reset.setFixedWidth(200)
        self.btn_reset.setFixedHeight(38)
        self.btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(self.btn_reset)

        self.btn_open = QPushButton(t("settings_open_config"))
        self.btn_open.setObjectName("secondaryBtn")
        self.btn_open.setFixedWidth(180)
        self.btn_open.setFixedHeight(38)
        self.btn_open.clicked.connect(self._open_config)
        btn_row.addWidget(self.btn_open)
        lay.addLayout(btn_row)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#3fb950;font-size:12px;font-weight:bold;")
        lay.addWidget(self.status)

        self.info_lbl = QLabel(t("settings_info"))
        self.info_lbl.setStyleSheet(
            "color:#8b949e;font-size:11px;background:#161b22;"
            "border:1px solid #21262d;border-radius:6px;padding:12px;"
        )
        self.info_lbl.setWordWrap(True)
        lay.addWidget(self.info_lbl)

        lay.addStretch()
        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def retranslate(self, _lang: str = ""):
        self.grp_lang.setTitle(t("settings_lang_section"))
        for code, btn in self._lang_btns.items():
            btn.setChecked(code == current_lang())
        self.grp_api.setTitle(t("settings_api_keys"))
        self.grp_dir.setTitle(t("settings_folders"))
        self.grp_misc.setTitle(t("settings_misc"))
        self.lbl_rate.setText(t("settings_rate_label"))
        self.chk_autosave.setText(t("settings_autosave"))
        self.btn_save.setText(t("settings_save_btn"))
        self.btn_reset.setText(t("settings_reset_btn"))
        self.btn_open.setText(t("settings_open_config"))
        self.info_lbl.setText(t("settings_info"))

    def _change_lang(self, code: str):
        set_lang(code)
        for c, btn in self._lang_btns.items():
            btn.setChecked(c == code)
        lang_signal.changed.emit(code)

    def _key_field(self, parent_layout, label, config_key, hint=""):
        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = QLabel(label + ":")
        lbl.setFixedWidth(160)
        lbl.setStyleSheet("color:#c9d1d9;font-size:12px;")
        row.addWidget(lbl)
        inp = QLineEdit()
        inp.setEchoMode(QLineEdit.EchoMode.Password)
        inp.setProperty("config_key", config_key)
        row.addWidget(inp)
        btn_show = QPushButton("👁")
        btn_show.setObjectName("secondaryBtn")
        btn_show.setFixedWidth(36)
        btn_show.setCheckable(True)
        btn_show.clicked.connect(lambda checked, i=inp: i.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        row.addWidget(btn_show)
        parent_layout.addLayout(row)
        if hint:
            h = QLabel(hint)
            h.setStyleSheet("color:#6e7681;font-size:10px;margin-left:166px;")
            parent_layout.addWidget(h)
        return inp

    def _dir_field(self, parent_layout, label, config_key):
        row = QHBoxLayout()
        lbl = QLabel(label + ":")
        lbl.setFixedWidth(220)
        lbl.setStyleSheet("color:#c9d1d9;font-size:12px;")
        row.addWidget(lbl)
        inp = QLineEdit()
        inp.setProperty("config_key", config_key)
        row.addWidget(inp)
        btn = QPushButton(t("settings_browse"))
        btn.setObjectName("secondaryBtn")
        btn.setFixedWidth(80)
        btn.clicked.connect(lambda _, i=inp: self._browse_dir(i))
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return inp

    def _browse_dir(self, inp):
        d = QFileDialog.getExistingDirectory(self, t("settings_folders"))
        if d:
            inp.setText(d)

    def _load(self):
        self.vt_inp.setText(Config.get("vt_api_key", ""))
        self.abuse_inp.setText(Config.get("abuseipdb_key", ""))
        self.results_inp.setText(Config.get("results_dir", ""))
        self.quar_inp.setText(Config.get("quarantine_dir", ""))
        self.rate_inp.setText(str(Config.get("vt_rate_limit_sec", 15)))
        self.chk_autosave.setChecked(Config.get("auto_save_reports", False))

    def _save_all(self):
        try:
            Config.set("vt_api_key",       self.vt_inp.text().strip())
            Config.set("abuseipdb_key",    self.abuse_inp.text().strip())
            Config.set("results_dir",      self.results_inp.text().strip())
            Config.set("quarantine_dir",   self.quar_inp.text().strip())
            try:
                Config.set("vt_rate_limit_sec", int(self.rate_inp.text().strip() or 15))
            except ValueError:
                Config.set("vt_rate_limit_sec", 15)
            Config.set("auto_save_reports", self.chk_autosave.isChecked())

            self.status.setText(t("settings_saved"))
            self.status.setStyleSheet("color:#3fb950;font-size:12px;font-weight:bold;")
            QTimer.singleShot(4000, lambda: self.status.setText(""))
        except Exception as e:
            self.status.setText(f"{t('error')}: {e}")
            self.status.setStyleSheet("color:#f85149;font-size:12px;font-weight:bold;")

    def _reset(self):
        msg = QMessageBox.question(
            self, t("settings_reset_confirm_title"), t("settings_reset_confirm_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if msg == QMessageBox.StandardButton.Yes:
            Config.reset()
            self._load()
            self.status.setText(t("settings_reset_done"))
            self.status.setStyleSheet("color:#d29922;font-size:12px;font-weight:bold;")

    def _open_config(self):
        if Config._path and os.path.exists(Config._path):
            os.startfile(Config._path)
        else:
            Config.save()
            if Config._path:
                os.startfile(Config._path)

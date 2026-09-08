import requests
from PyQt6.QtCore import QThread, pyqtSignal
from config import Config


class NetIntelWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)
    def __init__(self, target):
        super().__init__()
        self.target = target.strip()
    def run(self):
        try:
            out = {"target": self.target, "abuse": None, "whois": None, "geo": None}
            # AbuseIPDB
            try:
                r = requests.get(
                    "https://api.abuseipdb.com/api/v2/check",
                    headers={"Key": Config.get("abuseipdb_key"), "Accept": "application/json"},
                    params={"ipAddress": self.target, "maxAgeInDays": 90, "verbose": True},
                    timeout=10
                )
                if r.status_code == 200:
                    out["abuse"] = r.json().get("data", {})
            except Exception as e:
                out["abuse_err"] = str(e)
            # ip-api.com (бесплатно, без ключа)
            try:
                r2 = requests.get(
                    f"http://ip-api.com/json/{self.target}?fields=status,country,regionName,city,isp,org,as,query",
                    timeout=8
                )
                if r2.status_code == 200:
                    out["geo"] = r2.json()
            except Exception as e:
                out["geo_err"] = str(e)
            self.result.emit(out)
        except Exception as e:
            self.error.emit(str(e))

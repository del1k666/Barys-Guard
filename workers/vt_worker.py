import os
import time
import requests
from PyQt6.QtCore import QThread, pyqtSignal
from config import Config, VT_URL
from core.hash_utils import compute_sha256


class VTWorker(QThread):
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)
    def __init__(self, hash_value):
        super().__init__()
        self.hash_value = hash_value.strip()
    def run(self):
        try:
            r = requests.get(VT_URL.format(self.hash_value),
                             headers={"x-apikey": Config.get("vt_api_key")}, timeout=15)
            if r.status_code == 200:   self.result.emit(r.json())
            elif r.status_code == 404: self.error.emit("Hash not found in VirusTotal database.")
            elif r.status_code == 401: self.error.emit("Invalid API key.")
            elif r.status_code == 429: self.error.emit("Rate limit exceeded. Try again later.")
            else: self.error.emit(f"HTTP {r.status_code}")
        except requests.exceptions.Timeout:
            self.error.emit("Request timed out.")
        except Exception as e:
            self.error.emit(str(e))


class BulkHashWorker(QThread):
    """Считает SHA256 для пачки файлов и проверяет каждый через VT с rate limit"""
    progress  = pyqtSignal(int, int, str)   # current, total, name
    file_done = pyqtSignal(dict)
    all_done  = pyqtSignal(int)

    def __init__(self, files):
        super().__init__()
        self.files = files
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = len(self.files)
        for i, fpath in enumerate(self.files):
            if self._stop:
                break
            name = os.path.basename(fpath)
            self.progress.emit(i + 1, total, name)

            # Считаем SHA256
            try:
                h = compute_sha256(fpath)
            except Exception as e:
                self.file_done.emit({
                    "file": fpath, "sha256": "ERROR",
                    "status": "ERROR", "mal": 0, "total": 0,
                    "name": name
                })
                continue

            # VT запрос
            try:
                r = requests.get(
                    VT_URL.format(h),
                    headers={"x-apikey": Config.get("vt_api_key")},
                    timeout=15
                )
                if r.status_code == 200:
                    data = r.json()
                    attrs = data.get("data", {}).get("attributes", {})
                    stats = attrs.get("last_analysis_stats", {})
                    mal = stats.get("malicious", 0)
                    sus = stats.get("suspicious", 0)
                    tot = sum(stats.values())
                    if mal >= 5:
                        status = "MALICIOUS"
                    elif mal > 0 or sus > 0:
                        status = "SUSPICIOUS"
                    else:
                        status = "CLEAN"
                    self.file_done.emit({
                        "file": fpath, "sha256": h, "status": status,
                        "mal": mal, "total": tot, "name": name
                    })
                elif r.status_code == 404:
                    self.file_done.emit({
                        "file": fpath, "sha256": h, "status": "NOT_FOUND",
                        "mal": 0, "total": 0, "name": name
                    })
                elif r.status_code == 429:
                    # Rate limit — ждём минуту
                    time.sleep(60)
                    continue  # повторим этот файл
                else:
                    self.file_done.emit({
                        "file": fpath, "sha256": h,
                        "status": f"HTTP {r.status_code}",
                        "mal": 0, "total": 0, "name": name
                    })
            except Exception as e:
                self.file_done.emit({
                    "file": fpath, "sha256": h,
                    "status": "ERROR", "mal": 0, "total": 0,
                    "name": name
                })

            # Rate limit для бесплатного API: 4 запроса в минуту = 15 сек между запросами
            if i < total - 1:
                time.sleep(15)

        self.all_done.emit(total)

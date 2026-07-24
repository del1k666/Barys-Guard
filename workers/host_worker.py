import base64
import concurrent.futures
import os
import threading
import time
import requests
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from config import Config, VT_URL
from core.agent_client import AgentClient
from core.i18n import t


class PingWorker(QThread):
    """Pings all hosts in parallel; emits (host_id, is_online, info_dict)."""
    result = pyqtSignal(str, bool, dict)

    def __init__(self, hosts: list):
        super().__init__()
        self._hosts = hosts

    def _ping_one(self, h: dict):
        client = AgentClient(h["ip"], h["port"], h["token"], timeout=5)
        try:
            info = client.ping()
            return h["id"], True, info
        except Exception:
            return h["id"], False, {}

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(self._ping_one, h): h for h in self._hosts}
            for fut in concurrent.futures.as_completed(futures):
                host_id, online, info = fut.result()
                self.result.emit(host_id, online, info)


class RemoteScanWorker(QThread):
    """Runs selected scan types on a single remote host."""
    progress = pyqtSignal(str)
    done     = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, host: dict, scan_types: list, path: str, rules: dict):
        """
        scan_types: subset of ["yara", "ioc", "hashes"]
        rules: {name: rule_text} for YARA scans
        """
        super().__init__()
        self._host       = host
        self._scan_types = scan_types
        self._path       = path
        self._rules      = rules

    def run(self):
        client  = AgentClient(self._host["ip"], self._host["port"], self._host["token"], timeout=120)
        results = []
        try:
            if "yara" in self._scan_types and self._rules:
                self.progress.emit(t("scan_progress_yara"))
                r = client.scan_yara(self._rules, self._path)
                for m in r.get("matches", []):
                    results.append({"type": "YARA", "rule": m.get("rule", "?"),
                                    "file": m.get("file", "?")})

            if "ioc" in self._scan_types:
                self.progress.emit(t("scan_progress_ioc"))
                r = client.scan_ioc()
                for p in r.get("processes", []):
                    if p.get("suspicious"):
                        results.append({
                            "type": "IOC",
                            "rule": t("scan_suspicious_process"),
                            "file": f"{p['name']} (PID:{p['pid']}) {p['path']}",
                        })
                for c in r.get("connections", []):
                    results.append({
                        "type": "IOC",
                        "rule": t("scan_connection"),
                        "file": f"{c['process']} → {c['remote']}",
                    })
                for a in r.get("autoruns", []):
                    results.append({
                        "type": "IOC",
                        "rule": t("scan_autorun"),
                        "file": f"{a['name']} = {a['value']}",
                    })

            if "hashes" in self._scan_types:
                self.progress.emit(t("scan_progress_hashes"))
                r = client.scan_hashes(self._path)
                for h in r.get("hashes", []):
                    results.append({
                        "type":   "HASH",
                        "rule":   (h.get("sha256") or "?")[:16] + "…",
                        "sha256": h.get("sha256", ""),
                        "file":   h.get("file", "?"),
                    })

            self.done.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RemoteProcessListWorker(QThread):
    """Fetches running process list from remote agent."""
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, host: dict):
        super().__init__()
        self._host = host

    def run(self):
        client = AgentClient(self._host["ip"], self._host["port"],
                             self._host["token"], timeout=30)
        try:
            r = client.list_processes()
            self.done.emit(r.get("processes", []))
        except Exception as e:
            self.error.emit(str(e))


class RemoteMemScanWorker(QThread):
    """Runs /scan/memory/all on remote agent with selected YARA rules."""
    progress = pyqtSignal(str)
    done     = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, host: dict, rules: dict):
        super().__init__()
        self._host  = host
        self._rules = rules

    def stop(self):
        self.requestInterruption()

    def run(self):
        client = AgentClient(self._host["ip"], self._host["port"],
                             self._host["token"], timeout=600)
        try:
            self.progress.emit("Сканирование памяти процессов на удалённом хосте...")
            r = client.scan_memory_all(self._rules)
            if "error" in r:
                self.error.emit(r["error"])
                return
            results = []
            for m in r.get("matches", []):
                results.append({
                    "type":         "MEMORY",
                    "rule":         m.get("rule", "?"),
                    "file":         m.get("file", "?"),
                    "pid":          str(m.get("pid", "?")),
                    "process_name": m.get("process_name", "?"),
                })
            self.done.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RemoteHashVTWorker(QThread):
    """Checks a list of {sha256, file} dicts against VirusTotal (15 s rate limit)."""
    progress  = pyqtSignal(str)
    file_done = pyqtSignal(dict)   # {sha256, file, status, mal, total}
    all_done  = pyqtSignal()

    def __init__(self, hashes: list):
        super().__init__()
        self._hashes = hashes
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        total = len(self._hashes)
        for i, item in enumerate(self._hashes):
            if self._stop_event.is_set():
                break
            sha256 = item.get("sha256", "")
            fpath  = item.get("file",   "")
            self.progress.emit(f"VT {i+1}/{total}: {os.path.basename(fpath)}")

            result = None
            for attempt in range(3):
                try:
                    r = requests.get(
                        VT_URL.format(sha256),
                        headers={"x-apikey": Config.get("vt_api_key")},
                        timeout=15,
                    )
                    if r.status_code == 200:
                        attrs = r.json().get("data", {}).get("attributes", {})
                        stats = attrs.get("last_analysis_stats", {})
                        mal = stats.get("malicious", 0)
                        sus = stats.get("suspicious", 0)
                        tot = sum(stats.values())
                        status = "MALICIOUS" if mal >= 5 else "SUSPICIOUS" if (mal or sus) else "CLEAN"
                        result = {"sha256": sha256, "file": fpath,
                                  "status": status, "mal": mal, "total": tot}
                        break
                    elif r.status_code == 404:
                        result = {"sha256": sha256, "file": fpath,
                                  "status": "NOT_FOUND", "mal": 0, "total": 0}
                        break
                    elif r.status_code == 429:
                        self.progress.emit(
                            f"VT: rate limit — ожидание 60 с... (попытка {attempt+1}/3)")
                        for _ in range(60):
                            if self._stop_event.is_set():
                                break
                            time.sleep(1)
                        # loop continues to retry
                    else:
                        result = {"sha256": sha256, "file": fpath,
                                  "status": f"HTTP {r.status_code}", "mal": 0, "total": 0}
                        break
                except Exception:
                    result = {"sha256": sha256, "file": fpath,
                              "status": "ERROR", "mal": 0, "total": 0}
                    break

            self.file_done.emit(result or {"sha256": sha256, "file": fpath,
                                           "status": "RATE_LIMIT", "mal": 0, "total": 0})

            if i < total - 1 and not self._stop_event.is_set():
                for _ in range(15):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)   # free VT API: 4 req/min

        self.all_done.emit()


class DeployWorker(QThread):
    """Copies agent.exe to remote host via WinRM and installs it as a service."""
    progress = pyqtSignal(str)
    done     = pyqtSignal(str)   # emits the token read from the remote host
    error    = pyqtSignal(str)

    def __init__(self, ip: str, username: str, password: str):
        super().__init__()
        self._ip       = ip
        self._username = username
        self._password = password

    def _agent_exe(self) -> Path:
        candidates = [
            Path(__file__).parent.parent / "agent" / "dist" / "agent.exe",
            Path(__file__).parent.parent / "agent" / "agent.exe",
        ]
        for p in candidates:
            if p.exists():
                return p
        raise FileNotFoundError(
            "agent.exe not found. Build it first:\n"
            "  cd agent && build.bat"
        )

    def run(self):
        try:
            import winrm
        except ImportError:
            self.error.emit("pywinrm not installed. Run: pip install pywinrm")
            return

        try:
            agent_path = self._agent_exe()
        except FileNotFoundError as e:
            self.error.emit(str(e))
            return

        try:
            # Try HTTPS first (port 5986), fall back to HTTP (5985) with warning
            try:
                session = winrm.Session(
                    f"https://{self._ip}:5986/wsman",
                    auth=(self._username, self._password),
                    transport="ntlm",
                    server_cert_validation="ignore",
                    operation_timeout_sec=30,
                    read_timeout_sec=60,
                )
                self.progress.emit(f"Подключение к {self._ip} по WinRM (HTTPS)...")
                session.run_ps("echo test")  # test connection
            except Exception:
                self.progress.emit(f"HTTPS недоступен, подключение по HTTP (небезопасно)...")
                session = winrm.Session(
                    f"http://{self._ip}:5985/wsman",
                    auth=(self._username, self._password),
                    transport="ntlm",
                    operation_timeout_sec=30,
                    read_timeout_sec=60,
                )
            # Clear password from memory as soon as the session is established
            self._password = None

            self.progress.emit("Создание директории C:\\Program Files\\IOCAgent...")
            res = session.run_ps(
                "New-Item -ItemType Directory -Force "
                "-Path 'C:\\Program Files\\IOCAgent' | Out-Null"
            )
            if res.status_code != 0:
                self.error.emit(f"Не удалось создать директорию: {res.std_err.decode('utf-8', errors='replace')}")
                return

            self.progress.emit(f"Копирование {agent_path.name} ({agent_path.stat().st_size // 1024} КБ)...")
            session.run_ps(
                "[System.IO.File]::WriteAllBytes("
                "'C:\\Program Files\\IOCAgent\\agent.exe', [byte[]]@())"
            )
            try:
                CHUNK = 512 * 1024  # 512 KB raw bytes per read
                B64_CHUNK = 4000    # base64 chars per WinRM call
                file_size = agent_path.stat().st_size
                bytes_sent = 0
                with open(agent_path, "rb") as f:
                    while True:
                        raw_chunk = f.read(CHUNK)
                        if not raw_chunk:
                            break
                        b64_chunk = base64.b64encode(raw_chunk).decode()
                        bytes_sent += len(raw_chunk)
                        # Send the b64 data in smaller WinRM-safe pieces
                        for j in range(0, len(b64_chunk), B64_CHUNK):
                            part = b64_chunk[j: j + B64_CHUNK]
                            pct = min(bytes_sent * 100 // file_size, 100) if file_size else 100
                            self.progress.emit(f"Загрузка агента... {pct}%")
                            res = session.run_ps(
                                f"$b=[Convert]::FromBase64String('{part}');"
                                "$f=[IO.File]::Open('C:\\Program Files\\IOCAgent\\agent.exe','Append');"
                                "$f.Write($b,0,$b.Length);$f.Close()"
                            )
                            if res.status_code != 0:
                                session.run_ps("Remove-Item 'C:\\Program Files\\IOCAgent\\agent.exe' -Force -EA SilentlyContinue")
                                self.error.emit(f"Ошибка при копировании файла: {res.std_err.decode('utf-8', errors='replace')}")
                                return
            except Exception as e:
                self.error.emit(f"Ошибка передачи файла: {e}")
                return

            self.progress.emit("Установка Windows Service...")
            res = session.run_ps(
                "cd 'C:\\Program Files\\IOCAgent'; .\\agent.exe --install"
            )
            if res.status_code != 0:
                self.error.emit(f"Ошибка установки сервиса: {res.std_err.decode('utf-8', errors='replace')}")
                return
            session.run_ps("Start-Service IOCAgent -ErrorAction SilentlyContinue")

            self.progress.emit("Чтение токена...")
            r = session.run_ps("Get-Content 'C:\\Program Files\\IOCAgent\\token.txt'")
            if r.status_code != 0 or not r.std_out:
                self.error.emit("Не удалось прочитать токен. Подождите несколько секунд и попробуйте снова.")
                return
            token = r.std_out.decode("utf-8").strip()
            if not token:
                self.error.emit("Токен пустой — агент мог не запуститься ещё. Повторите попытку.")
                return
            self.done.emit(token)
        except Exception as e:
            self.error.emit(str(e))


class NetworkIsolationWorker(QThread):
    """Runs a single network isolation action on a remote host."""
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, host: dict, action: str, mgmt_ip: str = ""):
        """
        action: "status" | "isolate" | "restore"
        """
        super().__init__()
        self._host    = host
        self._action  = action
        self._mgmt_ip = mgmt_ip

    def run(self):
        client = AgentClient(self._host["ip"], self._host["port"],
                             self._host["token"], timeout=30)
        try:
            if self._action == "isolate":
                r = client.network_isolate(self._mgmt_ip)
            elif self._action == "restore":
                r = client.network_restore()
            else:
                r = client.network_status()
            self.done.emit(r)
        except Exception as e:
            self.error.emit(str(e))


class RemoteInfoWorker(QThread):
    """Fetches live system metrics from remote agent via /info."""
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, host: dict):
        super().__init__()
        self._host = host

    def run(self):
        client = AgentClient(self._host["ip"], self._host["port"],
                             self._host["token"], timeout=15)
        try:
            self.done.emit(client.get_info())
        except Exception as e:
            self.error.emit(str(e))

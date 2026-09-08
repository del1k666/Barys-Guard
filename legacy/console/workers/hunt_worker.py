import concurrent.futures
from PyQt6.QtCore import QThread, pyqtSignal
from core.agent_client import AgentClient
from core.hosts_config import load_hosts


class HuntWorker(QThread):
    result   = pyqtSignal(str, dict)
    progress = pyqtSignal(str)
    done     = pyqtSignal()

    def __init__(self, payload: dict):
        """payload: {mutex, hashes, hash_path, process_name} — all keys optional"""
        super().__init__()
        self._payload = payload

    def _hunt_one(self, host: dict) -> tuple:
        label = f"{host['name']} ({host['ip']})"
        try:
            client = AgentClient(host["ip"], host["port"], host["token"], timeout=60)
            r = client.hunt(self._payload)
            r["_online"] = True
            return label, r
        except Exception:
            return label, {"_online": False}

    def run(self):
        hosts = load_hosts()
        if not hosts:
            self.done.emit()
            return
        self.progress.emit(f"Опрос {len(hosts)} хостов...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            futures = {ex.submit(self._hunt_one, h): h for h in hosts}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    label, result = fut.result()
                    self.result.emit(label, result)
                except Exception:
                    pass
        self.done.emit()

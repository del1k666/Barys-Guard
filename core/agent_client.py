import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class AgentClient:
    def __init__(self, ip: str, port: int, token: str, timeout: int = 10):
        self._base    = f"https://{ip}:{port}"
        self._headers = {"X-Api-Token": token, "Content-Type": "application/json"}
        self._timeout = timeout

    def _get(self, path: str) -> dict:
        r = requests.get(
            f"{self._base}{path}",
            headers=self._headers,
            verify=False,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict) -> dict:
        r = requests.post(
            f"{self._base}{path}",
            json=data,
            headers=self._headers,
            verify=False,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def ping(self) -> dict:
        return self._get("/ping")

    def get_info(self) -> dict:
        return self._get("/info")

    def scan_yara(self, rules: dict, path: str) -> dict:
        return self._post("/scan/yara", {"rules": rules, "path": path})

    def scan_ioc(self) -> dict:
        return self._post("/scan/ioc", {})

    def scan_memory(self, pid: int = 0, rules: dict = None) -> dict:
        return self._post("/scan/memory", {"pid": pid, "rules": rules or {}})

    def scan_hashes(self, path: str) -> dict:
        return self._post("/scan/hashes", {"path": path})

    def list_processes(self) -> dict:
        return self._post("/scan/memory", {"pid": 0, "rules": {}})

    def scan_memory_all(self, rules: dict) -> dict:
        return self._post("/scan/memory/all", {"rules": rules})

    def network_status(self) -> dict:
        return self._get("/network/status")

    def network_isolate(self, mgmt_ip: str = "") -> dict:
        return self._post("/network/isolate", {"mgmt_ip": mgmt_ip})

    def network_restore(self) -> dict:
        return self._post("/network/restore", {})

    def hunt(self, payload: dict) -> dict:
        return self._post("/hunt", payload)

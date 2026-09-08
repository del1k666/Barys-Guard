import os
import json
import subprocess
import tempfile
from PyQt6.QtCore import QThread, pyqtSignal


class ProcessListWorker(QThread):
    """Собирает список процессов через PowerShell с деталями"""
    result = pyqtSignal(list)
    error  = pyqtSignal(str)

    def run(self):
        # try/catch is a statement in PS, not an expression — cannot be used
        # inside @{} directly; assign to vars first.
        # $path -match '^[A-Za-z]:' excludes UNC paths that cause
        # Get-AuthenticodeSignature to hang indefinitely on CRL fetches.
        script = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
Get-Process | ForEach-Object {
    $path = 'N/A'; $signed = 'N/A'; $cpu = 0.0; $memMB = 0.0; $comp = ''
    try { if ($_.Path) { $path = $_.Path } } catch {}
    try { $cpu   = [math]::Round($_.CPU, 1) } catch {}
    try { $memMB = [math]::Round($_.WorkingSet64 / 1MB, 1) } catch {}
    try { $comp  = $_.Company } catch {}
    if ($path -ne 'N/A' -and $path -match '^[A-Za-z]:' -and (Test-Path -LiteralPath $path)) {
        try {
            $s = Get-AuthenticodeSignature -LiteralPath $path
            $signed = if ($s.Status -eq 'Valid') { 'Signed' } else { 'Unsigned' }
        } catch { $signed = 'Unknown' }
    }
    [PSCustomObject]@{
        PID     = $_.Id
        Name    = $_.ProcessName
        Path    = $path
        CPU     = $cpu
        MemMB   = $memMB
        Signed  = $signed
        Company = $comp
    }
} | ConvertTo-Json -Compress
"""
        proc = None
        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace"
            )
            try:
                stdout, stderr = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                # taskkill /F /T kills the entire process tree including child powershell jobs
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=5
                )
                proc.kill()
                stdout, stderr = proc.communicate()
                self.error.emit("Превышено время ожидания (60с) — список может быть неполным")
                return

            if proc.returncode == 0 and stdout.strip():
                data = json.loads(stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                self.result.emit(data)
            else:
                self.error.emit(stderr.strip() or "Нет данных")
        except Exception as e:
            self.error.emit(str(e))


class MemScanWorker(QThread):
    """Сканирует память процессов YARA правилами"""
    progress = pyqtSignal(int, int, str)   # current, total, proc_name
    hit      = pyqtSignal(dict)            # {pid, name, rule, severity}
    done     = pyqtSignal(int)             # total hits
    error    = pyqtSignal(str)

    def __init__(self, pids_names, rules_dict, yara_exe):
        super().__init__()
        self.pids_names = pids_names   # [(pid, name), ...]
        self.rules_dict = rules_dict
        self.yara_exe   = yara_exe
        self._stop      = False

    def stop(self):
        self._stop = True

    def run(self):
        tmp_path = None
        hits = 0
        try:
            # Пишем все правила в один файл
            combined = "\n\n".join(self.rules_dict.values())
            fd, tmp_path = tempfile.mkstemp(suffix=".yar")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(combined)

            total = len(self.pids_names)
            for i, (pid, name) in enumerate(self.pids_names):
                if self._stop:
                    break
                self.progress.emit(i + 1, total, f"{name} (PID {pid})")
                try:
                    cmd = [self.yara_exe, "-p", tmp_path, str(pid)]
                    r = subprocess.run(
                        cmd, capture_output=True, text=True,
                        timeout=15, encoding="utf-8", errors="replace"
                    )
                    if r.stdout.strip():
                        for line in r.stdout.strip().splitlines():
                            line = line.strip()
                            if line and not line.startswith("warning"):
                                parts = line.split(" ", 1)
                                rule = parts[0] if parts else line
                                self.hit.emit({
                                    "pid": pid, "name": name,
                                    "rule": rule, "line": line
                                })
                                hits += 1
                except subprocess.TimeoutExpired:
                    pass
                except Exception:
                    pass
        except Exception as e:
            self.error.emit(str(e))
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try: os.unlink(tmp_path)
                except: pass
            self.done.emit(hits)

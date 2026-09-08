import os
import subprocess
import tempfile
from PyQt6.QtCore import QThread, pyqtSignal


POWERSHELL_SCRIPT = r"""
param([string]$ResultDir)
if (-not (Test-Path $ResultDir)) {{ New-Item -ItemType Directory -Path $ResultDir | Out-Null }}
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$output = @()
$output += "=== IOC COLLECTION REPORT ==="
$output += "Time: $(Get-Date)"
$output += "ResultDir: $ResultDir"
$output += ""

$expectedPaths = @("C:\Windows\System32","C:\Windows\SysWOW64","C:\Program Files","C:\Program Files (x86)")
$processes = Get-Process | ForEach-Object {{
    try {{ $path = $_.Path }} catch {{ $path = "N/A" }}
    $suspicious = $false
    if ($path -and $path -ne "N/A") {{
        $match = $expectedPaths | Where-Object {{ $path -like "$_*" }}
        if (-not $match) {{ $suspicious = $true }}
    }}
    [PSCustomObject]@{{ Time=(Get-Date).ToString("s"); Name=$_.ProcessName; PID=$_.Id;
        Path=if($path){{$path}}else{{"N/A"}}; Suspicious=$suspicious }}
}}
$suspCount = ($processes | Where-Object {{ $_.Suspicious }}).Count
$output += "--- PROCESSES ---"
$output += "Total: $($processes.Count) | Suspicious: $suspCount"
$processes | Export-Csv "$ResultDir\processes_$Timestamp.csv" -NoTypeInformation -Encoding UTF8
$processes | Where-Object {{ $_.Suspicious }} | ForEach-Object {{
    $output += "[SUSPICIOUS] $($_.Name) (PID:$($_.PID)) -> $($_.Path)"
}}

$output += ""
$output += "--- AUTORUNS ---"
$regPaths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run"
)
$autoruns = @()
foreach ($reg in $regPaths) {{
    try {{
        $vals = Get-ItemProperty -Path $reg -ErrorAction SilentlyContinue
        if ($vals) {{
            $vals.PSObject.Properties | Where-Object {{ $_.Name -notlike "PS*" }} | ForEach-Object {{
                $autoruns += [PSCustomObject]@{{ Source=$reg; Name=$_.Name; Value=$_.Value }}
            }}
        }}
    }} catch {{}}
}}
$output += "Autorun entries: $($autoruns.Count)"
$autoruns | Export-Csv "$ResultDir\autoruns_$Timestamp.csv" -NoTypeInformation -Encoding UTF8
$autoruns | ForEach-Object {{ $output += "[AUTORUN] $($_.Name) = $($_.Value)" }}

$output += ""
$output += "--- NETWORK CONNECTIONS ---"
$connections = Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | ForEach-Object {{
    [PSCustomObject]@{{ LocalAddr=$_.LocalAddress; LocalPort=$_.LocalPort;
        RemoteAddr=$_.RemoteAddress; RemotePort=$_.RemotePort; PID=$_.OwningProcess;
        Process=(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName }}
}}
$output += "Active connections: $($connections.Count)"
$connections | Export-Csv "$ResultDir\connections_$Timestamp.csv" -NoTypeInformation -Encoding UTF8
$connections | ForEach-Object {{ $output += "[NET] $($_.Process) -> $($_.RemoteAddr):$($_.RemotePort)" }}

$output += ""
$output += "=== DONE ==="
$output | Out-String
"""


class IOCWorker(QThread):
    log   = pyqtSignal(str)
    done  = pyqtSignal(str)
    error = pyqtSignal(str)
    def __init__(self, result_dir):
        super().__init__()
        self.result_dir = result_dir
    def run(self):
        try:
            script = POWERSHELL_SCRIPT.format()  # no extra vars needed
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ps1", mode="w", encoding="utf-8")
            tmp.write(script)
            tmp.close()
            self.log.emit("▶ Launching PowerShell collection...")
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", tmp.name,
                 "-ResultDir", self.result_dir],
                capture_output=True, text=True, timeout=60
            )
            os.unlink(tmp.name)
            if result.returncode != 0 and result.stderr.strip():
                self.error.emit(result.stderr.strip())
                return
            for line in result.stdout.strip().split("\n"):
                self.log.emit(line.rstrip())
            self.done.emit(self.result_dir)
        except subprocess.TimeoutExpired:
            self.error.emit("Script timed out after 60 seconds.")
        except FileNotFoundError:
            self.error.emit("PowerShell not found. Requires Windows.")
        except Exception as e:
            self.error.emit(str(e))

"""
BarysGuard Agent — remote scanning daemon.

  python agent.py                  run in foreground (test mode)
  python agent.py --install        install as Windows Service
  python agent.py --uninstall      remove Windows Service
  python agent.py --start          start the installed service
"""
import sys
import os
import re
import ssl
import json
import hmac
import stat
import secrets
import socket
import tempfile
import subprocess
import platform
import hashlib
import ipaddress
import ctypes
import concurrent.futures
from pathlib import Path
from datetime import datetime
from functools import wraps

import psutil
from flask import Flask, request, jsonify

# ── Paths ──────────────────────────────────────────────────────────────────
AGENT_VERSION = "1.0.0"
PORT          = 5555
AGENT_DIR     = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CERT_FILE     = AGENT_DIR / "cert.pem"
KEY_FILE      = AGENT_DIR / "key.pem"
TOKEN_FILE    = AGENT_DIR / "token.txt"

YARA_EXE: str | None = None
for _p in [AGENT_DIR / "yara64.exe", Path(r"C:\Tools\yara\yara64.exe")]:
    if _p.exists():
        YARA_EXE = str(_p)
        break

# ── Auth ───────────────────────────────────────────────────────────────────
_token: str = ""

def _load_token() -> str:
    global _token
    if TOKEN_FILE.exists():
        _token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    else:
        _token = secrets.token_hex(32)
        tmp = TOKEN_FILE.with_suffix(".tmp")
        tmp.write_text(_token, encoding="utf-8")
        tmp.replace(TOKEN_FILE)
    return _token


def _auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not hmac.compare_digest(request.headers.get("X-Api-Token", ""), _token):
            return jsonify({"error": "unauthorized"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Certificate ───────────────────────────────────────────────────────────
def _generate_cert() -> bool:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime as dt

        key  = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.utcnow())
            .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=3650))
            .sign(key, hashes.SHA256())
        )
        CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        # SEC-04: key is stored unencrypted (passphrase would require Flask ssl_context changes).
        # Mitigate by restricting file permissions to owner read/write only.
        KEY_FILE.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — owner read/write only
        return True
    except Exception:
        return False


# ── Flask app ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB max body


@app.route("/ping")
@_auth
def ping():
    return jsonify({
        "status":        "ok",
        "hostname":      socket.gethostname(),
        "os":            platform.platform(),
        "agent_version": AGENT_VERSION,
        "timestamp":     datetime.now().isoformat(),
    })


@app.route("/info")
@_auth
def info():
    mem  = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    return jsonify({
        "cpu_percent":  psutil.cpu_percent(interval=0.5),
        "ram_total":    mem.total,
        "ram_used":     mem.used,
        "ram_percent":  mem.percent,
        "disk_total":   disk.total,
        "disk_used":    disk.used,
        "disk_percent": disk.percent,
        "boot_time":    psutil.boot_time(),
        "os":           platform.platform(),
        "users":        [u.name for u in psutil.users()],
    })


@app.route("/scan/yara", methods=["POST"])
@_auth
def scan_yara():
    data       = request.json or {}
    rules_dict = data.get("rules", {})
    path       = data.get("path", r"C:\Users")

    if not rules_dict:
        return jsonify({"error": "no rules"}), 400
    if not os.path.exists(path):
        return jsonify({"error": f"path not found: {path}"}), 400

    matches = []
    if not YARA_EXE:
        matches.append({"rule": "INFO", "file": "yara64.exe not found on agent"})
        return jsonify({"matches": matches})

    for rule_name, rule_text in rules_dict.items():
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".yar")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rule_text)
            for fpath in _collect_files(path):
                try:
                    r = subprocess.run(
                        [YARA_EXE, tmp_path, fpath],
                        capture_output=True, text=True, timeout=15,
                        encoding="utf-8", errors="replace",
                    )
                    for line in r.stdout.strip().splitlines():
                        line = line.strip()
                        if line and " " in line:
                            parts = line.split(" ", 1)
                            matches.append({"rule": parts[0], "file": parts[1]})
                    if r.stderr.strip():
                        matches.append({"rule": "WARN", "file": f"[{rule_name}] {r.stderr.strip()[:150]}"})
                except subprocess.TimeoutExpired:
                    matches.append({"rule": "TIMEOUT", "file": fpath})
                except Exception as e:
                    matches.append({"rule": "ERROR", "file": str(e)})
        except Exception as e:
            matches.append({"rule": "COMPILE_ERR", "file": f"{rule_name}: {e}"})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    return jsonify({"matches": matches})


@app.route("/scan/ioc", methods=["POST"])
@_auth
def scan_ioc():
    PS = r"""
$out = @()
$procs = Get-Process | ForEach-Object {
    try { $p = $_.Path } catch { $p = 'N/A' }
    $sus = $p -and $p -ne 'N/A' -and $p -notlike 'C:\Windows\*' -and $p -notlike 'C:\Program Files*'
    [PSCustomObject]@{Name=$_.ProcessName;PID=$_.Id;Path=if($p){$p}else{'N/A'};Suspicious=$sus}
}
$procs | ForEach-Object { $out += "PROC|$($_.Name)|$($_.PID)|$($_.Path)|$($_.Suspicious)" }
$nets = Get-NetTCPConnection -State Established -EA SilentlyContinue
$nets | ForEach-Object {
    $pn = (Get-Process -Id $_.OwningProcess -EA SilentlyContinue).ProcessName
    $out += "NET|$pn|$($_.LocalAddress):$($_.LocalPort)|$($_.RemoteAddress):$($_.RemotePort)"
}
@('HKLM:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run') | ForEach-Object {
    $rk = $_
    try {
        $v = Get-ItemProperty $rk -EA SilentlyContinue
        if ($v) { $v.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
            ForEach-Object { $out += "REG|$($_.Name)|$($_.Value)" } }
    } catch {}
}
$out -join "`n"
"""
    result = {"processes": [], "connections": [], "autoruns": [], "error": ""}
    try:
        r = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", PS],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        for line in r.stdout.strip().splitlines():
            parts = line.split("|")
            if not parts:
                continue
            if parts[0] == "PROC" and len(parts) >= 5:
                result["processes"].append({
                    "name": parts[1], "pid": parts[2],
                    "path": parts[3], "suspicious": parts[4].lower() == "true",
                })
            elif parts[0] == "NET" and len(parts) >= 4:
                result["connections"].append(
                    {"process": parts[1], "local": parts[2], "remote": parts[3]}
                )
            elif parts[0] == "REG" and len(parts) >= 3:
                result["autoruns"].append({"name": parts[1], "value": parts[2]})
    except Exception as e:
        result["error"] = str(e)

    return jsonify(result)


@app.route("/scan/memory", methods=["POST"])
@_auth
def scan_memory():
    data       = request.json or {}
    pid        = data.get("pid", 0)
    rules_dict = data.get("rules", {})

    if not pid:
        procs = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                procs.append({
                    "pid":  p.info["pid"],
                    "name": p.info["name"],
                    "exe":  p.info.get("exe") or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return jsonify({"processes": procs})

    matches = []
    if YARA_EXE and rules_dict:
        try:
            exe_path = psutil.Process(int(pid)).exe()
        except Exception:
            return jsonify({"error": f"cannot access process {pid}"}), 400

        for rule_name, rule_text in rules_dict.items():
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".yar")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(rule_text)
                r = subprocess.run(
                    [YARA_EXE, tmp_path, exe_path],
                    capture_output=True, text=True, timeout=15,
                )
                for line in r.stdout.strip().splitlines():
                    if " " in line.strip():
                        parts = line.strip().split(" ", 1)
                        matches.append({"rule": parts[0], "file": parts[1]})
            except Exception as e:
                matches.append({"rule": "ERROR", "file": str(e)})
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

    return jsonify({"matches": matches})


@app.route("/scan/memory/all", methods=["POST"])
@_auth
def scan_memory_all():
    data       = request.json or {}
    rules_dict = data.get("rules", {})

    if not rules_dict:
        return jsonify({"error": "no rules"}), 400
    if not YARA_EXE:
        return jsonify({"error": "yara64.exe not found on agent"}), 400

    processes = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            processes.append({"pid": p.info["pid"], "name": p.info["name"]})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Combine all rules into one file so yara runs once per process, not once per rule×process
    combined = "\n\n".join(rules_dict.values())
    matches  = []
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".yar")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(combined)

        # Verify the combined file compiles before scanning all processes
        probe = subprocess.run(
            [YARA_EXE, tmp_path, str(os.getpid())],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        if probe.returncode > 1:
            err = probe.stderr.strip()[:200] or "compile error"
            return jsonify({"error": f"YARA compile error: {err}"}), 400

        def _scan_proc(proc):
            try:
                r = subprocess.run(
                    [YARA_EXE, tmp_path, str(proc["pid"])],
                    capture_output=True, text=True, timeout=20,
                    encoding="utf-8", errors="replace",
                )
                hits = []
                for line in r.stdout.strip().splitlines():
                    line = line.strip()
                    if line and " " in line:
                        parts = line.split(" ", 1)
                        hits.append({
                            "rule":         parts[0],
                            "file":         parts[1],
                            "pid":          proc["pid"],
                            "process_name": proc["name"],
                        })
                return hits
            except subprocess.TimeoutExpired:
                return [{"rule": "TIMEOUT", "file": f"pid:{proc['pid']}",
                         "pid": proc["pid"], "process_name": proc["name"]}]
            except Exception as e:
                return [{"rule": "ERROR", "file": str(e),
                         "pid": proc["pid"], "process_name": proc["name"]}]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for hits in pool.map(_scan_proc, processes):
                matches.extend(hits)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return jsonify({"matches": matches})


@app.route("/hunt", methods=["POST"])
@_auth
def hunt():
    import ctypes
    data         = request.json or {}
    mutex_name   = data.get("mutex", "")
    hashes       = data.get("hashes", [])
    hash_path    = data.get("hash_path", "C:\\")
    process_name = data.get("process_name", "")

    result = {}

    def _file_hash(fpath: str) -> str:
        h = hashlib.sha256()
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    if mutex_name:
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenMutexW(SYNCHRONIZE, False, mutex_name)
        found = bool(handle)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        result["mutex_found"] = found
        result["mutex_name"]  = mutex_name

    if process_name:
        matches = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"].lower() == process_name.lower():
                    matches.append({"name": p.info["name"], "pid": p.info["pid"]})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        result["process_matches"] = matches

    if hashes:
        norm = {h.lower() for h in hashes if len(h) in (32, 64)}
        found_files = []
        file_count  = 0
        try:
            for root, _dirs, files in os.walk(hash_path):
                if file_count >= 10000:
                    break
                for fname in files:
                    if file_count >= 10000:
                        break
                    fpath = os.path.join(root, fname)
                    try:
                        if os.path.getsize(fpath) > 500 * 1024 * 1024:
                            continue
                        sha256 = _file_hash(fpath)
                        if sha256 in norm:
                            found_files.append({"file": fpath, "hash": sha256})
                        file_count += 1
                    except (PermissionError, OSError):
                        pass
        except Exception:
            pass
        result["hash_matches"] = found_files

    return jsonify(result)


@app.route("/scan/hashes", methods=["POST"])
@_auth
def scan_hashes():
    data  = request.json or {}
    path  = data.get("path", r"C:\Users")
    items = []
    for fpath in _collect_files(path)[:200]:
        try:
            h = hashlib.sha256()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            items.append({"file": fpath, "sha256": h.hexdigest()})
        except (PermissionError, OSError):
            pass
    return jsonify({"hashes": items})


# ── Network Isolation ──────────────────────────────────────────────────────
# Only the Allow rules — blocking is done via profile default action, not explicit rules.
# Block rules in Windows Firewall override Allow rules at the same priority,
# so using Set-NetFirewallProfile ensures mgmt IP can always reach the agent.
_ISOLATE_NAMES = [
    "IOCIsolate_AllowMgmt_In",
    "IOCIsolate_AllowMgmt_Out",
]

def _ps(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-NonInteractive",
         "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )

def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


@app.route("/network/status", methods=["GET"])
@_auth
def network_status():
    try:
        r = _ps(
            "if (Get-NetFirewallRule -DisplayName 'IOCIsolate_AllowMgmt_In' "
            "-ErrorAction SilentlyContinue) { 'isolated' } else { 'normal' }",
            timeout=10,
        )
        isolated = "isolated" in r.stdout.lower()
        return jsonify({"isolated": isolated})
    except Exception as e:
        return jsonify({"isolated": False, "error": str(e)})


@app.route("/network/isolate", methods=["POST"])
@_auth
def network_isolate():
    if not _is_admin():
        return jsonify({
            "status": "error",
            "errors": ["Agent is not running as Administrator. "
                       "Restart the agent with elevated privileges to modify firewall rules."]
        }), 500

    data    = request.get_json(silent=True) or {}
    mgmt_ip = data.get("mgmt_ip", "").strip()
    if not mgmt_ip:
        return jsonify({
            "status": "error",
            "errors": ["mgmt_ip is required — without it there is no way to restore isolation remotely."]
        }), 400
    try:
        ipaddress.ip_address(mgmt_ip.strip())
        mgmt_ip = mgmt_ip.strip()
    except ValueError:
        return jsonify({"error": "Invalid management IP"}), 400

    try:
        # Remove stale rules first
        names_ps = ",".join(f"'{n}'" for n in _ISOLATE_NAMES)
        _ps(f"@({names_ps}) | ForEach-Object {{ "
            f"Remove-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }}")

        # Strategy: allow mgmt IP first, THEN set profile default to Block.
        # This avoids the Windows Firewall priority issue where explicit Block rules
        # always override Allow rules — profile-default Block does NOT override Allow rules.
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "try {\n"
            f"    New-NetFirewallRule -DisplayName 'IOCIsolate_AllowMgmt_In' "
            f"-Direction Inbound -Action Allow -RemoteAddress '{mgmt_ip}' "
            f"-Enabled True -Profile Any | Out-Null\n"
            f"    New-NetFirewallRule -DisplayName 'IOCIsolate_AllowMgmt_Out' "
            f"-Direction Outbound -Action Allow -RemoteAddress '{mgmt_ip}' "
            f"-Enabled True -Profile Any | Out-Null\n"
            "    Set-NetFirewallProfile -Profile Domain,Private,Public "
            "-DefaultInboundAction Block -DefaultOutboundAction Block\n"
            "} catch {\n"
            "    Write-Host \"ERROR: $($_.Exception.Message)\"\n"
            "    exit 1\n"
            "}"
        )
        r = _ps(script)
        if r.returncode != 0:
            err = (r.stdout or r.stderr).strip()[:400]
            return jsonify({"status": "error", "errors": [err or "PowerShell firewall script failed"]}), 500
        return jsonify({"status": "isolated", "mgmt_ip": mgmt_ip})
    except Exception as e:
        return jsonify({"status": "error", "errors": [str(e)]}), 500


@app.route("/network/restore", methods=["POST"])
@_auth
def network_restore():
    try:
        # Remove allow rules
        names_ps = ",".join(f"'{n}'" for n in _ISOLATE_NAMES)
        _ps(f"@({names_ps}) | ForEach-Object {{ "
            f"Remove-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }}")
        # Restore profile defaults (NotConfigured = OS default: inbound block, outbound allow)
        _ps("Set-NetFirewallProfile -Profile Domain,Private,Public "
            "-DefaultInboundAction NotConfigured -DefaultOutboundAction NotConfigured")
    except Exception:
        pass
    return jsonify({"status": "restored"})


def _collect_files(path: str, limit: int = 10_000) -> list:
    if os.path.isfile(path):
        return [path]
    files = []
    try:
        for root, _, fnames in os.walk(path):
            for fn in fnames:
                files.append(os.path.join(root, fn))
                if len(files) >= limit:
                    return files
    except Exception:
        pass
    return files


# ── Server startup ─────────────────────────────────────────────────────────
def run_server():
    _load_token()
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        print("[IOCAgent] Generating self-signed certificate...")
        _generate_cert()
    print(f"[IOCAgent] v{AGENT_VERSION} starting on https://0.0.0.0:{PORT}")
    print(f"[IOCAgent] Token: {_token}")
    print(f"[IOCAgent] YARA:  {YARA_EXE or 'NOT FOUND — scans will fail'}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    app.run(host="0.0.0.0", port=PORT, ssl_context=ctx,
            debug=False, use_reloader=False, threaded=True)


# ── Windows Service ────────────────────────────────────────────────────────
def _run_as_service():
    import win32serviceutil
    import win32service
    import win32event
    import servicemanager

    class IOCAgentService(win32serviceutil.ServiceFramework):
        _svc_name_         = "IOCAgent"
        _svc_display_name_ = "BarysGuard Agent"
        _svc_description_  = "Remote scanning agent for BarysGuard"

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            run_server()

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(IOCAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(IOCAgentService)


if __name__ == "__main__":
    args = sys.argv[1:]
    if any(a in args for a in ["--install", "--uninstall", "--start", "--stop", "--remove"]):
        _run_as_service()
    else:
        run_server()

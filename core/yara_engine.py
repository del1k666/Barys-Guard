import os
import subprocess
import tempfile

# yara-python: добавляем AppData/Roaming путь (pip --user установка)
import sys as _sys
import os as _os

_username = _os.environ.get('USERNAME', 'user')
_ver = f'{_sys.version_info.major}{_sys.version_info.minor}'
_user_site = (
    f'C:\\Users\\{_username}\\AppData\\Roaming\\Python'
    f'\\Python{_ver}\\site-packages'
)
if _user_site not in _sys.path:
    _sys.path.insert(0, _user_site)

try:
    import yara as _yara_module
    YARA_PYTHON_AVAILABLE = True
except Exception:
    _yara_module = None
    YARA_PYTHON_AVAILABLE = False


def _collect_files(path: str) -> list:
    """Собирает все файлы из папки рекурсивно или возвращает сам файл."""
    if os.path.isfile(path):
        return [path]
    files = []
    for root, _, fnames in os.walk(path):
        for fn in fnames:
            files.append(os.path.join(root, fn))
    return files


def run_yara_scan(rules_dict: dict, target_path: str, results_dir: str) -> list:
    matches = []
    files   = _collect_files(target_path)

    if not files:
        matches.append({"rule": "INFO", "file": f"Файлов не найдено в: {target_path}"})
        return matches

    # Ищем yara64.exe
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level since yara64.exe is in the project root, not core/
    _project_dir = os.path.dirname(_script_dir)
    yara_exe = None
    for p in [
        os.path.join(_project_dir, "yara64.exe"),
        os.path.join(_project_dir, "yara", "yara64.exe"),
        r"C:\Tools\yara\yara64.exe",
        r"C:\Tools\yara.exe",
        r"C:\Program Files\YARA\yara64.exe",
    ]:
        if os.path.exists(p):
            yara_exe = p
            break

    # Диагностика
    matches.append({"rule": "DEBUG", "file": f"Движок: {yara_exe or 'НЕ НАЙДЕН'}"})
    matches.append({"rule": "DEBUG", "file": f"Файлов для скана: {len(files)}"})
    matches.append({"rule": "DEBUG", "file": f"Правил: {len(rules_dict)}"})

    if yara_exe:
        # Каждое правило отдельно — надёжнее чем все сразу
        for rule_name, rule_text in rules_dict.items():
            tmp_path = None
            try:
                # Пишем правило во временный файл
                fd, tmp_path = tempfile.mkstemp(suffix=".yar")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(rule_text)

                # Сканируем все файлы этим правилом
                for fpath in files:
                    try:
                        cmd = [yara_exe, tmp_path, fpath]
                        r = subprocess.run(
                            cmd, capture_output=True, text=True,
                            timeout=15, encoding="utf-8", errors="replace"
                        )
                        # returncode 0 = совпадений нет, 1 = есть совпадения
                        if r.stdout.strip():
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
                    try: os.unlink(tmp_path)
                    except: pass

    elif YARA_PYTHON_AVAILABLE:
        for name, text in rules_dict.items():
            try:
                compiled = _yara_module.compile(source=text)
                for fpath in files:
                    try:
                        hits = compiled.match(fpath, timeout=10)
                        for hit in hits:
                            matches.append({"rule": hit.rule, "file": fpath})
                    except Exception:
                        pass
            except Exception as e:
                matches.append({"rule": "COMPILE_ERR", "file": f"{name}: {e}"})
    else:
        matches.append({
            "rule": "INFO",
            "file": "yara64.exe не найден рядом с main.py и yara-python не установлен."
        })

    return matches

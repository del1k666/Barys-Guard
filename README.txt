============================================================
  BARYSGUARD — Threat Intelligence & Incident Response
  Diploma Project — AITU Cybersecurity School 2025
============================================================

ТРЕБОВАНИЯ:
─────────────────────────────────────────────
  - Windows 10/11
  - Python 3.11+
  - Интернет (для VT API)

УСТАНОВКА И СБОРКА:
─────────────────────────────────────────────
1. Установи Python 3.11+ (галочка "Add to PATH")

2. Запусти build.bat от администратора

3. Готово: dist\BarysGuard.exe

БЕЗ СБОРКИ (разработка):
─────────────────────────────────────────────
  pip install -r requirements.txt
  python main.py

YARA ДВИЖОК:
─────────────────────────────────────────────
  Приоритет 1: yara64.exe в папке проекта
    Скачать: https://github.com/VirusTotal/yara/releases

  Приоритет 2: pip install yara-python

ВСТРОЕННЫЕ YARA ПРАВИЛА (40+):
─────────────────────────────────────────────
  CRITICAL: LockBit, BlackCat/ALPHV, Mimikatz,
            CobaltStrike, Meterpreter, WannaCry

  HIGH:     AsyncRAT, QuasarRAT, Remcos,
            RedLine Stealer, Vidar, AgentTesla,
            Sliver C2, Havoc C2, Chisel

  MEDIUM:   LOLBins, AMSI Bypass, DefenderTampering,
            WebShell_PHP, WebShell_ASPX, Packed_UPX

УДАЛЁННЫЙ АГЕНТ:
─────────────────────────────────────────────
  agent\dist\agent.exe — скопировать на целевой хост
  Запустить от администратора для генерации токена.
  Установить как службу: agent.exe --install

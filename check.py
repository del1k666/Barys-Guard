"""
Запусти этот файл ПЕРВЫМ: python check.py
Он покажет какие пакеты не установлены.
"""
import sys
print(f"Python: {sys.version}")
print(f"Path: {sys.executable}")
print()

errors = []

def test(name, import_str):
    try:
        exec(import_str)
        print(f"  [OK]  {name}")
    except Exception as e:
        print(f"  [!!]  {name} — ОШИБКА: {e}")
        errors.append((name, str(e)))

print("Проверка зависимостей:")
test("PyQt6",        "from PyQt6.QtWidgets import QApplication")
test("PyQt6.Core",   "from PyQt6.QtCore import QThread")
test("PyQt6.Gui",    "from PyQt6.QtGui import QFont")
test("requests",     "import requests")
test("yara-python",  "import yara")

print()
if errors:
    print("Не хватает:")
    for name, err in errors:
        if "yara" in name.lower():
            print(f"  pip install yara-python")
        elif "PyQt6" in name:
            print(f"  pip install PyQt6")
        elif "requests" in name:
            print(f"  pip install requests")
    print()
    print("Установи всё одной командой:")
    print("  pip install PyQt6 requests")
    print()
    print("yara-python опционален — приложение запустится без него,")
    print("но для YARA Scanner нужен yara64.exe в C:\\Tools\\yara\\")
else:
    print("Все зависимости установлены! Запускай: python main.py")

input("\nНажми Enter для выхода...")

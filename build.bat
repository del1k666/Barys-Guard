@echo off
echo ============================================
echo   BarysGuard -- Build Script
echo ============================================
echo.

cd /d "%~dp0"
echo [INFO] Папка: %CD%
echo.

echo [1] Установка зависимостей...
python -m pip install PyQt6 requests pyinstaller --quiet

echo [2] Сборка .exe...
python -m PyInstaller --onefile --windowed --name "BarysGuard" main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Сборка не удалась.
    echo ВАЖНО: Запусти build.bat БЕЗ прав администратора!
    pause & exit /b 1
)

echo.
echo ============================================
echo  ГОТОВО: dist\BarysGuard.exe
echo ============================================
pause

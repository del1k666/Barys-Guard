@echo off
cd /d "%~dp0"
echo Building IOC Agent...
pip install pyinstaller flask psutil cryptography pywin32
pyinstaller --onefile --name agent --hidden-import win32timezone agent.py
echo Done. agent.exe is in dist\
pause

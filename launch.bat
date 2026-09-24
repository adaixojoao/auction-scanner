@echo off
:: Starts the Auction Scanner app (no console window).
:: The desktop icon made by create_shortcut.bat does the same thing.
cd /d "%~dp0"
where pythonw >nul 2>nul && (start "" pythonw "%~dp0app.py") || (python "%~dp0app.py")

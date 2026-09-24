@echo off
:: Puts an "Auction Scanner" icon on the Desktop that opens the app.
:: Run this once from inside the auction-scanner folder (and again if you move it).

set FOLDER=%~dp0
set SHORTCUT=%USERPROFILE%\Desktop\Auction Scanner.lnk

:: pythonw runs the app without a console window.
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set PYW=%%P
if not defined PYW (
    echo Could not find pythonw.exe. Is Python installed and on PATH?
    pause
    exit /b 1
)

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath = '%PYW%';" ^
  "$s.Arguments = '\"%FOLDER%app.py\"';" ^
  "$s.WorkingDirectory = '%FOLDER%';" ^
  "$s.Description = 'Auction Scanner - find properties below market price';" ^
  "$s.IconLocation = '%FOLDER%icon.ico';" ^
  "$s.Save()"

echo.
echo  ============================================
echo   "Auction Scanner" is on your Desktop.
echo   Double-click it to open the app.
echo  ============================================
echo.
pause

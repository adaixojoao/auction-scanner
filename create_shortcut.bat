@echo off
:: Creates a desktop shortcut for Auction Scanner
:: Run this ONCE from inside the auction-scanner folder

set FOLDER=%~dp0
set SHORTCUT=%USERPROFILE%\Desktop\Auction Scanner.lnk
set LAUNCHER=%FOLDER%launch.bat

:: Create the actual launcher script
echo @echo off > "%LAUNCHER%"
echo title Auction Scanner >> "%LAUNCHER%"
echo cd /d "%FOLDER%" >> "%LAUNCHER%"
echo echo. >> "%LAUNCHER%"
echo echo  ============================================ >> "%LAUNCHER%"
echo echo   AUCTION SCANNER - Starting up... >> "%LAUNCHER%"
echo echo  ============================================ >> "%LAUNCHER%"
echo echo. >> "%LAUNCHER%"
echo echo  [1/3] Scraping Portuguese sources... >> "%LAUNCHER%"
echo python scraper.py --country PT --max-price 100000 --cartas --cartas-top 20 >> "%LAUNCHER%"
echo echo. >> "%LAUNCHER%"
echo echo  [2/3] Launching dashboard... >> "%LAUNCHER%"
echo echo  Opening browser in 3 seconds... >> "%LAUNCHER%"
echo timeout /t 3 /nobreak ^>nul >> "%LAUNCHER%"
echo start "" "http://127.0.0.1:8050" >> "%LAUNCHER%"
echo start "" "http://127.0.0.1:8050/cartas-review" >> "%LAUNCHER%"
echo echo. >> "%LAUNCHER%"
echo echo  [3/3] Dashboard running. >> "%LAUNCHER%"
echo echo  Listings:  http://127.0.0.1:8050 >> "%LAUNCHER%"
echo echo  Cartas:    http://127.0.0.1:8050/cartas-review >> "%LAUNCHER%"
echo echo. >> "%LAUNCHER%"
echo echo  Press Ctrl+C to stop. >> "%LAUNCHER%"
echo echo  ============================================ >> "%LAUNCHER%"
echo python dashboard.py >> "%LAUNCHER%"
echo pause >> "%LAUNCHER%"

:: Create the .lnk shortcut using PowerShell
powershell -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath = '%LAUNCHER%';" ^
  "$s.WorkingDirectory = '%FOLDER%';" ^
  "$s.Description = 'Auction Scanner - Find properties below market price';" ^
  "$ico = '%FOLDER%icon.ico';" ^
  "if (Test-Path $ico) { $s.IconLocation = $ico };" ^
  "$s.Save()"

echo.
echo  ============================================
echo   Shortcut created on your Desktop!
echo   Double-click "Auction Scanner" to start.
echo  ============================================
echo.
pause

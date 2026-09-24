@echo off
title Auction Scanner
cd /d "%~dp0"
echo.
echo  ============================================
echo   AUCTION SCANNER - Starting up...
echo  ============================================
echo.
echo  [1/3] Scraping Portuguese sources...
python scraper.py --country PT --max-price 100000 --cartas --cartas-top 20
echo.
echo  [2/3] Launching dashboard...
echo  Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8050"
start "" "http://127.0.0.1:8050/cartas-review"
echo.
echo  [3/3] Dashboard running.
echo  Listings:  http://127.0.0.1:8050
echo  Cartas:    http://127.0.0.1:8050/cartas-review
echo.
echo  Press Ctrl+C to stop.
echo  ============================================
python dashboard.py
pause

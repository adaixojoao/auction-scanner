@echo off
cd /d "%~dp0"
python scraper.py --source all --max-price 50000
echo.
echo Opening full report...
start "" "%~dp0report.md"
pause

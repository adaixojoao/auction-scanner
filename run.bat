@echo off
cd /d "%~dp0"
echo === EU Auction Scanner ===
echo.
python scraper.py --source all --max-price 50000 --analyze
echo.
echo Done. Reports: report.md + analysis.md
pause

@echo off
cd /d "%~dp0"
python scraper.py --source eleiloes --max-price 50000 --analyze
echo.
echo Done. Reports: report.md + analysis.md
pause

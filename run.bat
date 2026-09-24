@echo off
cd /d "%~dp0"
echo.
echo  Auction Scanner - Portugal Deep Discount Mode
echo  Budget: EUR 100,000
echo.

python scraper.py --country PT --max-price 100000

echo.
echo  Sealed-bid opportunities:
echo.
python scraper.py --report-only --sealed-bid --max-price 100000

echo.
echo Opening full report...
start "" "%~dp0report.docx"
pause

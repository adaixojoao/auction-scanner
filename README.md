# EU Auction Scanner

Scrapes judicial auction and forced-sale platforms across 6 European countries, scores listings by investment potential, and generates reports.

## Supported Sources

| Country | Source | Type |
|---------|--------|------|
| PT | [e-leiloes.pt](https://e-leiloes.pt) | Judicial auctions (REST API) |
| PT | [leilosoc.com](https://leilosoc.com) | Private auction house |
| PT | [citius.mj.pt](https://www.citius.mj.pt) | Court forced sales |
| PT | BCP Millennium | Bank repossessions |
| ES | [subastas.boe.es](https://subastas.boe.es) | Judicial auctions |
| FR | [licitor.com](https://www.licitor.com) | Judicial auctions |
| IT | [astegiudiziarie.it](https://www.astegiudiziarie.it) | Judicial auctions |
| HR | [e-oglasna.pravosudje.hr](https://e-oglasna.pravosudje.hr) | Court notices |
| HR | [FINA Ocevidnik](https://ponip.fina.hr/ocevidnik-web/) | Forced-sale registry (CSV) |
| NL | [openbareverkoop.nl](https://www.openbareverkoop.nl) | Public auctions |
| NL | [veilingnotaris.nl](https://veilingnotaris.nl) | Execution auctions |
| PT | [idealista.pt](https://www.idealista.pt) | Listings (Selenium) |

## Setup

```bash
pip install -r requirements.txt
```

Optional: set `ANTHROPIC_API_KEY` for AI-powered analysis (falls back to rule-based scoring without it).

## Usage

```bash
# Scrape all sources, generate report
python scraper.py

# Scrape a single source
python scraper.py --source eleiloes

# Scrape all sources for one country
python scraper.py --country PT

# Set budget
python scraper.py --max-price 30000

# Only regenerate report from existing DB
python scraper.py --report-only

# Run LLM analysis on top listings
python scraper.py --analyze
```

Or use `run.bat` on Windows for the default run (all sources, max price 50k, opens report).

## Output

- **report.md** / **report.docx** / **report.pdf** — full report with tables grouped by country
- **analysis.md** — investment analysis (AI or rule-based)
- **auctions.db** — SQLite database with all scraped listings

## Investment Scoring

Each listing is scored 0-100 based on:
- Property type (full dwelling vs fractional share)
- Price range and bid activity
- Location (urban vs rural)
- Red flags (usufruct, ruins, suspiciously cheap)
- Time pressure (ending soon)

## License

MIT

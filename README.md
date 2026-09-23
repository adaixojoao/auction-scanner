# EU Auction Scanner

Scrapes judicial auction and forced-sale platforms across 6 European countries, scores listings by investment potential, sends alerts, and provides a web dashboard.

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

# Send email alerts for high-scoring listings
python scraper.py --notify

# Scrape + launch web dashboard
python scraper.py --dashboard
```

Or use `run.bat` on Windows for the default run (all sources, max price 50k, opens report).

## Web Dashboard

Browse, filter, and sort all listings in a dark-themed web UI:

```bash
python dashboard.py
# Opens at http://127.0.0.1:8050
```

Features:
- Filter by country, source, price, score, type, and keyword search
- Sort by any column (score, price, bid, end date)
- Country flags, score badges (green/yellow/grey), NEW tags
- Responsive — works on mobile
- Stats cards: total listings, properties, countries, new today, high scorers

## Email Alerts

Get notified when high-scoring listings appear. Configure in `config.json`:

```json
{
  "notifications": {
    "enabled": true,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "you@gmail.com",
    "smtp_password": "your-app-password",
    "from_email": "you@gmail.com",
    "to_emails": ["you@gmail.com"],
    "min_score": 60,
    "send_on": "new"
  }
}
```

Then run with `--notify` or set `"enabled": true` for automatic alerts on every run.

## Scheduled Runs

Automatically scrape on a schedule using Windows Task Scheduler:

```bash
# Install task (runs every 6 hours by default)
python scheduler.py install

# Check status
python scheduler.py status

# Remove task
python scheduler.py remove
```

Change interval in `config.json` under `schedule.interval_hours`.

## Proxy Rotation

Avoid IP bans by rotating through proxies. Add to `config.json`:

```json
{
  "proxies": {
    "enabled": true,
    "list": [
      "http://user:pass@proxy1:8080",
      "socks5://proxy2:1080"
    ],
    "rotate_every": 5
  }
}
```

## Filtering

Control what gets saved to the database. Add to `config.json`:

```json
{
  "filters": {
    "countries": ["PT", "ES"],
    "types": ["apartamento", "moradia"],
    "exclude_keywords": ["usufruto", "1/12", "ruína"],
    "min_area_m2": 30,
    "min_score": 40,
    "districts": ["Lisboa", "Porto", "Madrid"]
  }
}
```

Empty arrays = no filter (include everything).

## Configuration

All settings live in `config.json` (created on first use or manually). See `config.py` for all defaults. CLI flags override config values.

## Output

- **report.md** / **report.docx** / **report.pdf** — full report with tables grouped by country
- **analysis.md** — investment analysis (AI or rule-based)
- **auctions.db** — SQLite database with all scraped listings
- **Web dashboard** — interactive browser at localhost:8050

## Investment Scoring

Each listing is scored 0-100 based on:
- Property type (full dwelling vs fractional share)
- Price range and bid activity
- Location (urban vs rural)
- Red flags (usufruct, ruins, suspiciously cheap)
- Time pressure (ending soon)

## Project Structure

```
auction-scanner/
  scraper.py        — main scraper + report generation
  dashboard.py      — Flask web dashboard
  notifications.py  — email alert system
  scheduler.py      — Windows Task Scheduler setup
  config.py         — configuration loader
  proxy.py          — proxy rotation for requests
  config.json       — user settings (gitignored)
  requirements.txt  — Python dependencies
  run.bat           — quick-run script for Windows
```

## License

MIT

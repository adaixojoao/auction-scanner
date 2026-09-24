# EU Auction Scanner

Scrapes judicial auctions, forced sales, tax seizures and bank repossessions in
12 EU countries, scores every listing for deep-discount potential, alerts you
about the good ones and helps prepare the proposal letters (*cartas*) for
Portuguese court sales.

```
40 sources ──► SQLite (auctions.db) ──► one loader ──► report · dashboard · alerts · cartas
                     never deleted          scores, hides expired / duplicate / stale / filtered
```

## Quick start

```bash
pip install -r requirements.txt
copy config.example.json config.json      # then fill in proponente, telegram, …
python scraper.py --country PT            # scrape Portugal, write report.md/.docx/.pdf
python dashboard.py                       # http://127.0.0.1:8050
```

On Windows, `create_shortcut.bat` puts an *Auction Scanner* icon on the desktop
that scrapes Portugal, generates cartas and opens the dashboard (`launch.bat`).

## Command line

```bash
python scraper.py                        # every default source, all countries
python scraper.py --country PT           # one country …
python scraper.py --country ES,FR,IT     # … or several
python scraper.py --source citius        # one source
python scraper.py --list-sources         # what exists, by country
python scraper.py --health               # which scrapers are working (see below)
python scraper.py --report-only          # rebuild the report from the database
python scraper.py --max-price 30000      # budget for this run
python scraper.py --sealed-bid           # print carta-fechada listings with agent contacts
python scraper.py --cartas --cartas-top 20   # proposal PDFs for active Citius sales
python scraper.py --check-active         # which Citius processes are still "Em venda"
python scraper.py --analyze              # Claude verdicts on the top 25 (needs ANTHROPIC_API_KEY)
python scraper.py --notify / --digest    # e-mail alerts / weekly digest
```

## Dashboard

`python dashboard.py` → http://127.0.0.1:8050

| Page | What it is |
|---|---|
| `/` | All listings: filter, sort, search. **Show hidden** reveals what the filters, de-duplication and staleness rules are hiding, and why. |
| `/cartas-review` | Review, adjust and approve proposal letters; logs what you send. Warns when an offer is below the legal minimum for a carta-fechada sale. |
| `/map` | Portuguese listings on a map. |
| `/health` | Every source: last run, how many listings, errors, and how many runs in a row it has returned nothing. |

## Source health — read this

Many of the 40 scrapers were written from a site's URL without confirming the
page layout, and sites change. A scraper that silently returns 0 looks exactly
like "no listings today", so every run is recorded (`scrape_log`) and
classified:

- **ok** — returned listings last run
- **broken** — used to return listings, now returns none (usually a layout change)
- **never worked** — has never returned a listing (selectors unconfirmed)
- **error** — the site failed; the message says how

It is on `/health`, at the end of every report, in the console after each run,
and in the weekly Telegram summary. Fix a broken card-grid site by editing its
`CardSite` spec in `sources/<country>.py`.

## Sources

`python scraper.py --list-sources` is the authoritative list.

| Country | Sources |
|---|---|
| PT | e-leilões, Citius, Portal das Finanças, leilosoc, BCP, Whitestar, Novo Banco, CGD, Santander, BPI, Imobancos, Centro de Leilões, Bid Leiloeira · *idealista (Selenium, on request)* |
| ES | BOE subastas, AEAT, Sareb, Haya, Servihabitat, SubastasActivas |
| FR | licitor, Enchères Publiques |
| IT | astegiudiziarie, PVP Giustizia, Gobid Real, Astalegale |
| DE | zvg-portal, justiz-auktion, zwangsversteigerung.de |
| NL | openbareverkoop, veilingnotaris, veilingbiljet |
| BE · HR · GR · RO · PL · CY | biddit · e-oglasna, FINA · eauction · ANAF · komornik · DLS |
| EU | *CourtBid via Apify (needs `apify_token`, on request)* |

Polish and Romanian sites price in PLN/RON; only amounts marked € are read, so
most of those listings show no price.

## What gets shown

Listings are **never deleted**. Everything reads through `db.load_listings()`,
which hides a listing when it is:

1. **expired** — the sale date has passed (a date without a time counts until the end of that day);
2. a **duplicate** — same country + municipality, price within €500 and area within 5 m² as a more complete listing from another source;
3. **stale** — its source has scraped successfully for 3+ days without seeing it (sold or withdrawn);
4. **filtered** — excluded by `filters` in config.json (countries, keywords, area, types, districts);
5. **low score** — below `filters.min_score`.

Change a filter and the next page load reflects it; nothing has to be re-scraped.

## Scoring (0–100)

Starts at 50. Main signals:

- **Skip (score 0):** fractional shares (`1/2`, `1 / 2 (Um Meio)`, `½`, quota-parte, avos…) and usufruct.
- **Down:** occupied/tenanted, no road access, inheritance rights only, overheated bidding, parking/storage only, suspiciously cheap.
- **Up:** deep bid-to-value discount, no bids yet, price cut since first seen, sealed-bid sale, forced/tax sale, no or tiny minimum bid, full dwelling, vacant (*devoluto*), rural land, size, €1k–30k sweet spot, below the local €/m² estimate, ending within 3–7 days.

Keywords match whole words, accent-insensitively, and ignore negations
("não arrendado", "sem inquilinos"): *desocupado* is vacant, not occupied, and
*Casal do Mato* is not a *casa*.

## Alerts

- **Telegram** (`telegram` in config.json): new listings scoring ≥ `min_score`
  (one message each, or one digest when there are more than 5), a twice-daily
  list of sales ending within 4 days with no offer logged, a weekly summary with
  failing sources, and a message when you mark a carta as won.
- **E-mail** (`notifications`): same idea via SMTP; `--digest` for a weekly top 15.

A listing is alerted once per channel (`alert_log`); a failed send is retried next run.

## Scheduling

```bash
python scheduler.py install   # Windows Task Scheduler: runs `tick` every 30 min (no console window)
python scheduler.py status    # the task, plus when each job last ran
python scheduler.py due       # what would run now
python scheduler.py run       # no Task Scheduler: stay open and tick every 5 min
python scheduler.py pt|eu|morning|report   # run one job now
```

Each tick runs whatever is due: Portugal every 2 h, other countries every 6 h,
deadline checks at 08:00 and 20:00, weekly summary Monday 08:00 (all in
`schedule` in config.json). Missed slots are caught up on the next tick, and a
lock stops two ticks overlapping. Log: `scheduler.log`.

## Configuration

All settings live in `config.json` (gitignored); `config.example.json` shows
every key and `config.py` has the defaults. Command-line flags win.

| Key | |
|---|---|
| `max_price`, `max_bid` | Budget |
| `filters` | What is shown (see above) |
| `proponente` | Name, NIF, address, e-mail and town printed on every carta |
| `telegram`, `notifications` | Alerts |
| `schedule` | Scheduler timetable |
| `proxies` | Proxy rotation for all scrapers |
| `report.desktop_copy` | Also write Auction-Report.docx/.pdf to the Desktop |
| `dashboard` | Host, port, `debug` (keep off: Flask's debugger runs code from the browser) |
| `apify_token` | For `--source courtbid` |

## Cartas and the 85% rule

For Portuguese executive sales by *propostas em carta fechada* the announced
value is 85% of the *valor base*, and offers below it are normally not
accepted. The review page warns when an offer is under that line. The low
fixed offers `suggest_bid()` proposes make sense for *negociação particular*;
confirm the sale type with the agente de execução before sending.

## Development

```
scraper.py      CLI            sources/     one module per country + registry (sources/__init__.py)
common.py       HTTP, parsing  db.py        schema/migrations, upsert, load_listings(), health
scoring.py      the score      report.py    Markdown/Word/PDF report, console summary
dashboard.py    Flask app      cartas.py    proposal letters, Citius status check
telegram_alert.py, notifications.py, scheduler.py, analysis.py
```

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # offline: the suite refuses network access
python -m pyflakes *.py sources/ tests/
```

CI runs both on every push. Adding a source: see the docstring at the top of
`sources/__init__.py`. Rules for AI agents working here: [AGENTS.md](AGENTS.md).

## License

MIT

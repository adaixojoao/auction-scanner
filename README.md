# EU Auction Scanner

A desktop app that watches judicial auctions, forced sales, tax seizures and bank
repossessions in 12 EU countries, scores every listing for deep-discount
potential, and helps you prepare, send and track your offers.

```
40 sources ──scan──► SQLite (auctions.db) ──► Listings · Offers · Map · Sources · Settings
                     never deleted              one score, one set of rules, everywhere
```

## Install (once)

1. Install [Python 3.11+](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"**.
2. In this folder, open a terminal and run:
   ```
   pip install -r requirements.txt
   ```
3. Double-click **`create_shortcut.bat`**. An **Auction Scanner** icon appears on your Desktop.

## Use

Double-click **Auction Scanner** on the Desktop. The app opens in its own
window; close the window and it quits by itself a few minutes later (after
finishing a scan that is in progress). Opening it while it runs just opens
another window.

| Page | What it is for |
|---|---|
| **Listings** | Everything found, scored. ☆ shortlists a listing for an offer, ✕ dismisses it (restore it from *Show → Hidden*). *Export report* gives Word, PDF or Markdown. |
| **Offers** | Prepare a letter, check it, send it, and record what happened. Tabs: *To review* (strong candidates + your shortlist), *Sent*, *Closed* (won / lost / cancelled), *Rejected*. |
| **Map** | Portuguese listings by district. |
| **Sources** | Every site the scanner reads and whether it works. Run one source on demand. |
| **Settings** | Budget, what to hide, your details for letters, alerts, automatic scanning. |

**Scan now** (top right) scans Portugal or every country; progress shows
next to it. While the app is open it also scans on the timetable in
Settings (Portugal every 2 h, other countries every 6 h). Tick *Keep scanning
when the app is closed* to install a quiet Windows background task that does
the same when the app is closed.

### Offers, step by step

1. A listing you ☆ on Listings, or a strong court sale, appears under **To review**.
2. Pick an amount (presets or type one). The amount in words is written for you
   (*quatro mil euros*). The letter below updates as you type — it is built from
   your details in Settings and the listing, and it is exactly what the PDF and
   e-mail will contain.
3. **Download PDF** or **Open in e-mail** (addressed to the agente de execução
   when known).
4. **Mark as sent**. It moves to **Sent**; when you hear back, mark it
   **won**, **lost** or **cancelled**.

e-leilões listings are online auctions: the page links to the listing and
**Log my bid** records what you bid there, instead of a letter.

**The 85% rule.** In Portuguese executive sales by *propostas em carta fechada*
(and on e-leilões) the announced value is 85% of the *valor base*, and offers
below it are normally not accepted. The Offers page warns when an amount is
under that line. The low fixed amounts suggested by default make sense for
*negociação particular*; confirm the sale type with the agente de execução.

**AI check** (on Offers) asks Claude for a verdict, risks and a suggested bid.
It needs the `ANTHROPIC_API_KEY` environment variable.

## What gets shown

Listings are **never deleted**. Every page, the report and the alerts hide a
listing when it is:

1. **dismissed** by you;
2. **expired** — the sale date has passed (a date without a time counts until the end of that day);
3. a **duplicate** — same country + municipality, price within €500 and area within 5 m² of a more complete listing from another source;
4. **stale** — its source has scanned successfully for 3+ days without seeing it (sold or withdrawn);
5. **filtered** — countries, hidden words, minimum area (Settings);
6. **low score** — below the minimum score (Settings).

A shortlisted listing ignores 5 and 6: you picked it on purpose. *Listings →
Show → Hidden* shows what is hidden and why.

## Scoring (0–100)

Starts at 50.

- **Skip (score 0):** fractional shares (`1/2`, `1 / 2 (Um Meio)`, `½`, quota-parte, avos…) and usufruct.
- **Down:** occupied/tenanted, no road access, inheritance rights only, overheated bidding, parking/storage only, suspiciously cheap.
- **Up:** deep bid-to-value discount, no bids yet, price cut since first seen, sealed-bid sale, forced/tax sale, no or tiny minimum bid, full dwelling, vacant (*devoluto*), rural land, size, €1k–30k sweet spot, below the local €/m² estimate, ending within 3–7 days.

Words match whole words, accents ignored, and negations are understood:
*desocupado* is vacant, not occupied; *não arrendado* is not tenanted; *Casal
do Mato* is not a *casa*.

## Sources and their health

Many scrapers were written from a site's address without confirming the page
layout, and sites change. A scraper that silently finds nothing looks exactly
like "no listings today", so every run is recorded and the **Sources** page
says which state each is in: **ok**, **broken** (worked before, finds nothing
now), **never worked**, or **error** (with the reason in plain words). Failing
sources are also listed in the report and the weekly Telegram summary.

| Country | Sources |
|---|---|
| PT | e-leilões, Citius, Portal das Finanças, leilosoc, BCP, Whitestar, Novo Banco, CGD, Santander, BPI, Imobancos, Centro de Leilões, Bid Leiloeira · *idealista (Selenium, on request)* |
| ES | BOE subastas, AEAT, Sareb, Haya, Servihabitat, SubastasActivas |
| FR | licitor, Enchères Publiques |
| IT | astegiudiziarie, PVP Giustizia, Gobid Real, Astalegale |
| DE | zvg-portal, justiz-auktion, zwangsversteigerung.de |
| NL | openbareverkoop, veilingnotaris, veilingbiljet |
| BE · HR · GR · RO · PL · CY | biddit · e-oglasna, FINA · eauction · ANAF · komornik · DLS |
| EU | *CourtBid via Apify (needs `apify_token` in config.json, on request)* |

Polish and Romanian sites price in PLN/RON; only amounts marked € are read.

## Alerts

Set up in **Settings**:

- **Telegram** — new listings above a score (one message each, or one digest
  when there are more than 5), a twice-daily list of sales ending within 4 days
  with no offer sent, a weekly summary, and a message when you mark an offer won.
- **E-mail** — the same new-listing alerts by SMTP.

Each listing is alerted once per channel; a failed send is retried next time.

## Files

| | |
|---|---|
| `auctions.db` | Everything found, your decisions and your offers. Back it up. |
| `config.json` | Your settings (written by the Settings page). |
| `reports/` | The latest report (`.md`, `.docx`, `.pdf`). |
| `app.log`, `scheduler.log` | What the app and the background task did. |

All of these stay on your computer and are ignored by git.

## Command line (optional)

Everything the app does is also available from a terminal:

```bash
python scraper.py --country PT           # scan Portugal and write the report
python scraper.py --country ES,FR        # several countries
python scraper.py --source citius        # one source
python scraper.py --list-sources
python scraper.py --health               # the Sources page, in the terminal
python scraper.py --report-only
python scraper.py --cartas --cartas-top 20   # batch: PDF letters for active Citius sales
python scraper.py --check-active         # which Citius processes are still "Em venda"
python scraper.py --analyze              # Claude verdicts on the top 25
python scheduler.py due | tick | status  # the timetable
python dashboard.py                      # the web interface without the desktop window
```

## Development

```
app.py          desktop launcher      dashboard.py   Flask routes (templates/, static/)
pipeline.py     the one scan routine  sources/       one module per country + registry
db.py           schema, load_listings common.py      HTTP, parsing, matching
scoring.py      the score             cartas.py      the one letter builder (+ PDF)
report.py       report files          analysis.py    Claude calls
scheduler.py    timetable             telegram_alert.py, notifications.py
```

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # offline: the suite refuses network access
python -m pyflakes *.py sources/ tests/
```

CI runs both on every push. Rules for AI agents working here: [AGENTS.md](AGENTS.md).

## License

MIT

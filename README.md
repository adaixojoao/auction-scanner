# EU Auction Scanner

A desktop app that watches judicial auctions, forced sales, tax seizures and bank
repossessions in 12 EU countries, scores every listing for deep-discount
potential, and helps you prepare, send and track your offers.

```
40 sources ──scan──► SQLite (auctions.db) ──► Listings · Offers · Map · Sources · Settings
                     never deleted              one score, one set of rules, everywhere
```

## Install (once)

1. Install [Python 3.11+](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** —
   and [Git for Windows](https://git-scm.com/download/win).
2. Open a terminal where you want the app and run:
   ```
   git clone https://github.com/adaixojoao/auction-scanner.git
   cd auction-scanner
   pip install -r requirements.txt
   ```
   (Installing with `git clone` is what lets the app update itself.)
3. Double-click **`create_shortcut.bat`**. An **Auction Scanner** icon appears on your Desktop.

## Updates

New versions are published on GitHub, in the `master` branch. Each time the app
starts, it checks GitHub and, if there is a newer version:

1. copies your database to `backups/` (the last 5 copies are kept);
2. updates the code — a clean fast-forward only;
3. installs any new packages from `requirements.txt`;
4. restarts itself with the new version, and says so once.

**Settings → Updates** shows the version you have, lists the changes waiting,
and has **Update now**. During a scan, the update waits for the scan to finish.
Untick *Update automatically when the app starts* to update only by hand.
From a terminal: `python updater.py` (check) or `python updater.py apply`.

It never overwrites your data (`auctions.db`, `config.json`, `reports/` and
logs are not in git) and never overwrites files you changed in the app
folder: then it tells you why it did not update. It skips the update when
offline, when git is missing, or when the folder was not installed with
`git clone`, and the app starts as it is.

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
| **Settings** | What you are looking for (rural plot size and price), budget, what to hide, your details for letters, alerts, automatic scanning. |

**Scan now** (top right) scans Portugal or every country; progress shows
next to it. While the app is open it also scans on the timetable in
Settings (Portugal every 2 h, other countries every 6 h). Tick *Keep scanning
when the app is closed* to install a quiet Windows background task that does
the same when the app is closed.

### Offers, step by step

1. A listing you ☆ on Listings, or a strong sale where the offer is a letter,
   appears under **To review**.
2. The page says how that sale is actually bid on, and offers the letters that
   fit it (pick one at the top of the letter):

   | Sale | How you bid | Letters |
   |---|---|---|
   | 🇵🇹 Court sale (Citius) | Sealed letter, or negotiation with the agente de execução | Offer (carta fechada / negociação particular / adjudicação) · Information request |
   | 🇵🇹 e-leilões, Finanças, auction houses | Online, on the site | Information request (e-leilões) · **Log my bid** |
   | 🇵🇹 Banks (Novo Banco, CGD, BPI…) | Negotiation | Purchase offer to the bank |
   | 🇪🇸 BOE court and tax auctions | Online at subastas.boe.es (Cl@ve or certificate, 5% deposit) | Information request to the court (occupancy, visits, charges, debts) · **Log my bid** |
   | 🇪🇸 Sareb, Haya, Servihabitat | Negotiation | Purchase offer (oferta de compra) |
   | 🇫🇷 Court sales (licitor, Enchères Publiques) | Only a lawyer at that court can bid, at the hearing | Instructions to your lawyer with your maximum (mandat) · Information request to the seller's lawyer (cahier des conditions de vente, visits, occupancy) |
   | 🇩🇪 Zwangsversteigerung (zvg) | In person at the Amtsgericht hearing, 10% security | Request to the court (Gutachten, whether the hearing goes ahead, occupancy, how to pay the security) · **Log my bid** |
   | 🇮🇹 Court sales (PVP, astegiudiziarie…) | Formal offer with a deposit, online on the PVP or in a sealed envelope | Request to the custode / delegato (visit, perizia, occupancy, condominium arrears) · **Log my offer** |
   | 🇳🇱 Executieveiling | Online, through the notary | Request to the notary (veilingvoorwaarden, viewing, tenants) · **Log my bid** |
   | 🇧🇪 biddit · 🇭🇷 FINA · 🇬🇷 eauction · 🇩🇪 justiz-auktion | Online | **Log my bid** |
   | 🇵🇱 🇷🇴 🇨🇾 | Not covered yet | General offer letter in English — check how offers must be made |

3. For an offer, pick an amount (presets or type one). Portuguese letters write
   it in words for you (*quatro mil euros*). The letter updates as you type. It
   is built from your details in Settings and the listing, and it is exactly
   what the PDF and e-mail contain. **Edit text** lets you change anything,
   such as adding your lawyer's name to a French mandat; the PDF and e-mail
   then use your version.
4. **Send by e-mail with PDF** sends it from the account in *Settings → E-mail*
   (a copy comes back to you), addressed to the court, agente or lawyer when
   the listing names one. Or **Download PDF** / **Open in my e-mail app** and
   then **Mark letter as sent** (e-mailed, posted, by hand, given to my lawyer).
5. It moves to **Sent**, which keeps the letter exactly as you sent it (and
   reprints that PDF). Mark an offer **won**, **lost** or **cancelled**; mark
   an information request **answered**, and the listing returns to *To review*
   so you can make the offer. A letter with no answer after 10 days is
   flagged there, and mentioned once in the Telegram morning message.

Online auctions, German hearings, Italian formal offers: make the bid where the
sale says, then **Log my bid** to track it.

**Calendar.** *📅 Deadlines to my calendar* (top of Offers) downloads the sale
dates of your shortlist and pending offers as a calendar file (Outlook, Google
Calendar, Apple Calendar), each with a reminder the day before; *Add to
calendar* on a listing does it for one sale. Times are converted from the
sale country's time zone.

**The 85% rule.** In Portuguese executive sales by *propostas em carta fechada*
(and on e-leilões) the announced value is 85% of the *valor base*, and offers
below it are normally not accepted. The Offers page warns when an amount is
under that line, when a French maximum is below the *mise à prix*, when an
Italian offer is below the *offerta minima* (75% of the base price) and when a
German bid is under the 50% / 70% limits of a first hearing. The
low fixed amounts suggested for Portuguese court sales make sense for
*negociação particular*; confirm the sale type with the agente de execução.
The guidance in the app is a summary, not legal advice.

**Details from the sale page.** For BOE auctions the scanner also reads the
authority, deposit and goods tabs (court name and e-mail, file number,
*situación posesoria*, visits); for licitor it opens each annonce (tribunal,
hearing date and time, *mise à prix*, the seller's lawyer, visits, occupancy).
Occupancy from these pages feeds the score. These parsers were written from
the sites' public layout and could not be tested against the live sites; the
Sources page shows if they stop finding anything.

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

The score says how well a listing fits the goal: **homes and plots at
ridiculous prices**. Urban plots are welcome; rural plots only when they are
**big and cheap**; homes in a **good location** that **do not need heavy work**.
Every listing is sorted into a kind (Listings → *What*): home, urban plot,
rural plot, or other.

Starts at 50.

- **Skip (score 0):** fractional shares (`1/2`, `1 / 2 (Um Meio)`, `½`, quota-parte, avos…) and usufruct.
- **Homes:** up for a home, good condition (*bom estado*, *renovado*, *pronto a
  habitar*…), a good location (*centro*, near the beach…, or a town we have
  local prices for) and being well below local prices per m². Down for needing
  some work, for an isolated location, and a lot for heavy work (*ruína*,
  *para recuperar*, *reconstrução*…).
- **Rural plots:** big (≥ 1 ha by default) and cheap (≤ €0.50/m² by default;
  half of that scores best). Smaller or dearer rural land is pushed down.
  Both limits are in **Settings → What you are looking for**.
- **Urban plots:** up. **Shops, garages, storage:** down.
- **Price:** up the lower the amount you would actually pay (current bid, else
  minimum, else price), for a deep bid-to-value discount, no bids yet and a
  price cut since first seen. Down for overheated bidding and suspiciously
  cheap junk.
- **Sale:** up for sealed-bid, forced and tax sales, no or tiny minimum bid,
  ending within 3–7 days. Down if occupied, no road access, or inheritance
  rights only.

What is not the goal stays under the default minimum score (45), however good
the sale looks: other (at most 35), rural plots that are too small (35), homes
needing heavy work (40). They are hidden unless you shortlist them.

Words match whole words, accents ignored, and negations are understood:
*desocupado* is vacant, not occupied; *não necessita de obras* does not count
as needing work; *Casal do Mato* is not a *casa*. The **AI check** is told the
same goal.

## Sources and their health

Many scrapers were written from a site's address without confirming the page
layout, and sites change. A scraper that silently finds nothing looks exactly
like "no listings today", so every run is recorded and the **Sources** page
says which state each is in: **ok**, **broken** (worked before, finds nothing
now), **never worked**, or **error** (with the reason in plain words). Failing
sources are also listed in the report and the weekly Telegram summary.

| Country | Sources |
|---|---|
| PT | e-leilões, Citius, leilosoc, BCP, Whitestar, CGD, Santander, BPI, Imobancos, Centro de Leilões, Bid Leiloeira · *idealista (Selenium, on request)* · not scanned: Portal das Finanças (login only), Novo Banco (portal closed) |
| ES | BOE subastas (includes AEAT tax auctions), Sareb, Haya, Servihabitat, SubastasActivas |
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
- **E-mail** — the same new-listing alerts by SMTP. The same account sends letters from the Offers page.

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
updater.py      updates from GitHub
pipeline.py     the one scan routine  sources/       one module per country + registry
db.py           schema, load_listings common.py      HTTP, parsing, matching
scoring.py      the score             letters.py     the one letter builder (+ PDF)
cartas.py       Citius batch letters  report.py      report files
analysis.py     Claude calls          ics_export.py  calendar files
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

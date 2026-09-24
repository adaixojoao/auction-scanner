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

**A version that does not start is rolled back.** If a new version crashes
while starting, or a start of it never completes, the app goes back to the
last version that started on this PC and restarts. It does not install that
version again until a newer one is published, and it tells you in the window
and in Settings → Updates. The database backup from before the update is in
`backups/` if you ever need it.

## Use

Double-click **Auction Scanner** on the Desktop. The app opens in its own
window; close the window and it quits by itself a few minutes later (after
finishing a scan that is in progress). Opening it while it runs just opens
another window.

| Page | What it is for |
|---|---|
| **Listings** | Everything found, scored; it shows at most the **best 100** (Settings → *Show at most*), and sorting and filters work within those. **ⓘ** opens everything known about a listing: description, case and court, agente / court / lawyer with contacts, deposit, occupancy, visits, rooms, the same sale on another site (a Citius case that is also on e-leilões), Spain's land registry, a map. Citius has no page per sale, so its title opens **How to find this sale on Citius** (court and case number to copy, the filters to set). ☆ shortlists a listing for an offer, ✕ dismisses it (restore it from *Show → Hidden*). *Export report* gives Word, PDF or Markdown. |
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
ridiculous prices**, in this order of preference:

1. a house in good condition, in a great location, well under market price;
2. a large farm plot next to water (river, stream, lake, reservoir), very cheap;
3. a house needing some repairs, dirt cheap, in a great location;
4. a medium farm plot next to water, dirt cheap;
5. a house in good condition, dirt cheap, in an ordinary location.

Not wanted: small or partial homes or plots, homes needing heavy work (unless
they come with a big farm plot, which is then what is scored), expensive
homes, bad locations. These examples are a test (`tests/test_scoring.py`), so
the order holds whenever the weights change.

Every listing is sorted into a kind (Listings → *What*): home, urban plot,
rural plot, or other. Starts at 50.

**Amounts score smoothly.** Price, size, discount to local prices, €/m² of
land, bid-to-value ratio, price cuts and days left each move the score along
a curve (`scoring.py`, the `*_POINTS` lists), so €20,000 scores a little more
than €20,001 instead of jumping at a step.

- **Skip (score 0):** fractional shares (`1/2`, `29/84`, `4986/100000`, *metade*, quota-parte, avos…), usufruct, and timeshares (*habitação periódica*, *multipropriedade*, *semana 37 de cada año*, *aprovechamiento por turno*, *multipropriété*…).
- **Homes:** up for good condition (*bom estado*, *renovado*…), a great
  location (*centro*, near the beach…) or a town we have local prices for,
  size up to about 150 m², and the discount to local prices per m². Down for
  needing some work, and a lot for an isolated location or heavy work
  (*ruína*, *para recuperar*…).
- **Rural plots:** size as a multiple of the minimum (1 ha by default; about
  5× scores as large), next to water, and €/m² against the maximum (€0.50/m²
  by default). Both limits are in **Settings → What you are looking for**.
  The absolute price counts half for land: its €/m² already says how cheap it is.
- **Urban plots:** up. **Shops, garages, storage:** down.
- **Price:** up the lower the amount you would actually pay (current bid, else
  minimum, else price); down above about €60,000. Up for a deep bid-to-value
  discount, no bids yet and a price cut since first seen.
- **Sale:** a sealed-bid (*carta fechada*) sale is a great chance (+20), and so
  is one where you name the price and none is published (+18). Up for forced
  and tax sales, a tiny minimum bid, ending soon. Down if occupied, no road
  access, or inheritance rights only.

What is not the goal stays under the default minimum score (45), however good
the sale looks: other (at most 35), homes needing heavy work or in an isolated
location (40). Small homes and plots and expensive homes are held down along a
curve: a 25 m² home at most 35, 40 m² at most 45, no limit from about 100 m²;
a home at €60,000 at most 100, €75,000 at most 55, €90,000 at most 40. They
are hidden unless you shortlist them.

Words match whole words, accents ignored, and negations are understood:
*desocupado* is vacant, not occupied; *não necessita de obras* does not count
as needing work; *Casal do Mato* is not a *casa*; *Rio Maior* is a town, not a
river. The **AI check** is told the same goal.

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
  Each new-listing message has **☆ Shortlist** and **✕ Dismiss** buttons (then
  **↩ Undo**), which do the same as in the app. Send **/top** for the best
  listings you have not decided on yet. While the app is open a tap works in
  seconds; when it is closed, the background task handles it on its next round
  (every 30 minutes). Only your own chat (the Chat ID in Settings) can use them.
- **Information requests, prepared for you** — after a scan, for strong sales
  (score 75+, at least 5 days before the end) where the recipient's e-mail is
  known (the agente de execução on e-leilões, the court on BOE, the seller's
  lawyer on licitor), the app prepares the information request and sends it to
  Telegram with **✉ Send**, **📄 Show letter** and **✕ Skip**. Nothing is sent
  until you tap Send; it then goes from your e-mail account with its PDF and
  appears under Offers → Sent. At most 5 new ones a day, each sale once.
  Settings → Information requests. It needs Telegram, the e-mail account, and
  your name and e-mail.
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

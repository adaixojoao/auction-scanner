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
| **Listings** | Everything found, scored; it shows at most the **best 100** (Settings → *Show at most*), and sorting and filters work within those. **ⓘ** opens everything known about a listing: description, case and court, agente / court / lawyer with contacts, deposit, occupancy, visits, rooms, the same sale on another site (a Citius case that is also on e-leilões), Spain's land registry, a map. **Where it is:** Street View and the satellite view at the property — from the sale's coordinates, or its address looked up on OpenStreetMap (always with the municipality; the panel says when the position is approximate). Add a free Google Maps Embed API key in Settings to see Street View inside the panel. **Official records:** for Portugal, the land-register numbers (description number, parish, conservatória, tax article) read from the sale, with Copy buttons and the steps to get the *certidão permanente* on Predial Online (you sign in and pay €15; it shows owners, area, composition and every charge); for Spain, what Catastro says (built area, plot, year, use), read automatically and compared with the sale. **What it really costs:** the taxes, fees and the work it needs, added to the price (below). Citius has no page per sale, so its title opens **How to find this sale on Citius** (court and case number to copy, the filters to set). ☆ shortlists a listing for an offer, ✕ dismisses it (restore it from *Show → Hidden*). *Export report* gives Word, PDF or Markdown. |
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

## What it really costs

A €20 000 house is not a €20 000 house. Under **ⓘ → What it really costs**
(and in the Telegram alert, and in the AI check) the scanner adds up what you
pay on top of the price:

- **Portugal**: IMT at the published brackets — 1% for a dwelling up to
  €104 261, 5% for a prédio rústico, 6.5% for anything else — plus imposto do
  selo (0.8%) and the land registry (€250). A bank or private sale also pays for
  the escritura; a court sale has none.
- **Other countries**: one typical transfer tax and notary/registry figure per
  country. Spain, Germany and Belgium set their rates by region, so those are a
  ballpark.
- **The work**, for a home whose floor area is known: €0–150/m² when the listing
  says it is in good condition, €300–600/m² when it says it needs work,
  €700–1 200/m² for a ruin, €200–500/m² when it says nothing. It is shown as a
  range because it is a guess.

All of it is an estimate. The Portuguese IMT is charged on the higher of the
price and the taxable value (VPT), which the listings do not publish, and the
brackets change with each Orçamento do Estado — they live in `costs.py`
(`IMT_YEAR`), one table to update.

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

- **Rejected (hidden, "rejected: …"):** unfinished buildings, properties not in the land register, occupied ones (any language), and land sold only together with another lot.
- **Skip (score 0):** fractional shares (`1/2`, `29/84`, `4986/100000`, *metade*, quota-parte, avos…), usufruct, and timeshares (*habitação periódica*, *multipropriedade*, *semana 37 de cada año*, *aprovechamiento por turno*, *multipropriété*…).
- **Homes:** up for good condition (*bom estado*, *renovado*…), **how far it
  is from town** (below), size up to about 150 m², and the discount to local
  prices per m². Down for
  needing some work, and a lot for an isolated location or heavy work
  (*ruína*, *para recuperar*…). Local prices in Portugal are the median price
  per m² of homes sold in each municipality (INE), from
  `data/pt_home_prices.csv`; refresh it with `python scripts/update_prices.py`
  on a PC that can reach ine.pt. Elsewhere, and for any municipality the file
  lacks, a small table of city prices is used. Each reason says which.
- **An old village house is not worth the town's median.** INE's median is
  mostly sound homes in town, so the local price is scaled per house before the
  discount is measured: condition (not stated 75%, some work 60%, heavy work
  35%), year built (1940 → 80%, 1980 → 90%) and distance from town (5 km →
  85%, 10 km → 70%). The reason says so: *57% below local prices (…; counted at
  54%: condition not stated, built 1937, 4 km from town)*.
- **Years on sale cost points.** Nobody bought it in all that time, which
  usually has a reason: up to −12 for years since the portal published it
  (Whitestar's detail page, read once per listing) and up to −8 for an old court
  case (the year in the case number).
- **A fração autónoma is a whole flat**, a home like any other (partial shares
  are *fração ideal*, *quota-parte*, and are skipped). One whose named use is a
  laundry, linen room, shop or garage is not a home, nor is a home's furniture
  (*recheio*) sold on its own.
- **Condition from the photos.** When the text says nothing about the state of
  a house, a vision model looks at its photos after a scan (the best homes, a
  few a scan, each once) and the reason says *(from the photos)*. By default an
  open model on this PC through [Ollama](https://ollama.com) (free and private:
  install it, then `ollama pull qwen2.5vl:3b`); or Claude with an Anthropic API
  key. Settings → Photo check.
- **Water from the map.** For a property with an exact position (the sale's
  coordinates or its street), OpenStreetMap is asked whether a river, stream,
  lake or reservoir is within 300 m; it counts like the words *junto ao rio*.
- **Local price by parish** where INE publishes one (the Porto and Lisbon
  areas, Setúbal, the Algarve, big cities); elsewhere the municipality's.
- **Unknown size** says *size unknown — ask* (−4), and a text that registers a
  property at the criminal registry is flagged *confirm with the court*.
- **Citius and e-leilões are one sale.** A Citius electronic auction is joined
  to its e-leilões page (case number, else municipality and exact base value):
  it gets the link, end date, photo and bids, and is listed once.
- **Reminders.** A starred listing gets a Telegram message three days before
  its sale ends and on the last day, with the 85% floor and the 5% cheque for a
  Portuguese sealed offer.
- **How far a house is from town.** "Good location" used to be guessed from
  words the listing often does not contain. When the property has a position on
  the map (its own coordinates, or the address found on OpenStreetMap) and the
  middle of its municipality's town has been looked up, the score uses the real
  distance: in the town is worth about as much as the old *centro* guess, 10 km
  out costs a little, and past 20 km the house is held down like an isolated one
  — a house nobody can reach services from is not what you are looking for. The
  panel shows it as *Distance to town*. A position no better than
  "municipality" is ignored (that pin **is** the town), and so is any distance
  over 40 km, which means the wrong town was found. Without a distance the old
  word-matching still applies.
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
- **On sale before:** when a property is back after an earlier round of its
  sale ended (same court case, same property; across Citius and e-leilões),
  nobody bought it then, so the seller is likely to take less. That is the
  first reason shown ("on sale before (ended 2026-08-12 at €40,000) — not
  sold then"), with "25% cheaper than the last round" when it is, and the
  listing's ⓘ panel links the earlier round. The scanner only knows rounds
  that ended since it was installed, so this finds more the longer it runs.

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
| PT | e-leilões, Citius, leilosoc, BCP, Whitestar, CGD, Santander, BPI, Imobancos, Centro de Leilões, Bid Leiloeira · *idealista (Selenium, on request)* · Portal das Finanças (with your account: Settings → Accounts) · not scanned: Novo Banco (portal closed) |
| ES | BOE subastas (includes AEAT tax auctions), Servihabitat (cheapest 20 per province), Haya, SubastasActivas · not scanned: Sareb (bot wall) |
| FR | licitor, Enchères Publiques |
| IT | astegiudiziarie, PVP Giustizia (search API), Astalegale (search API) · not scanned: Gobid Real (bot wall) |
| DE | zvg-portal, justiz-auktion, zwangsversteigerung.de |
| NL | openbareverkoop (each lot's page: size, year, use, date), veilingnotaris + vastgoedveiling (one platform, read as one; also its German lots) · not scanned: veilingbiljet (the same lots as openbareverkoop) |
| HR · PL | e-oglasna, FINA · komornik · not scanned (bot wall or broken certificate): biddit (BE), eauction (GR), ANAF (RO), DLS (CY) |
| EU | *CourtBid via Apify (needs `apify_token` in config.json, on request)* |

Polish and Romanian sites price in PLN/RON; only amounts marked € are read.

A site behind a bot wall (Cloudflare, Incapsula, an F5 firewall) is not worked around: it leaves the
default scan with the reason written next to it in `sources/`, and can still be run by name.

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
- **Price cuts** — when a listing's *valor base* falls by 5% or more between
  scans, Telegram says so with the old and the new price: <s>€40,000</s> →
  **€32,000**. Anything on your shortlist counts whatever it scores; everything
  else from score 60 up. Both numbers are in Settings → Telegram alerts. A bid
  going up is an auction working, not a discount, so it is not a cut. Each cut
  is sent once, and a second, deeper cut later is sent again.
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
A price cut is the one thing that can be sent about the same listing twice,
because a second cut is news again.

## Backup

Everything you have decided — the shortlist, the offers, the letters, the whole
history — is in `auctions.db`, on this PC only. **Settings → Backup** takes a
folder that is *not* on this PC (a OneDrive or Dropbox folder, another drive, a
memory stick) and copies the database there **once a day**, keeping the last 14
copies. **Back up now** does it immediately.

The copy is made with SQLite's own backup, so it is a whole, working database
even if the app is busy. To go back to one: close the app, then rename the copy
to `auctions.db` in the app's folder. The app also keeps a few copies in
`backups/` before each update — those are on the same disk, so they do not help
if the PC does.

## Files

| | |
|---|---|
| `auctions.db` | Everything found, your decisions and your offers. Settings → Backup copies it off this PC. |
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
costs.py        taxes, fees, work     prices.py      local €/m² (data/pt_home_prices.csv)
cartas.py       Citius batch letters  report.py      report files
analysis.py     Claude calls          ics_export.py  calendar files
geo.py          map positions, towns  listing_info.py  the ⓘ panel
scheduler.py    timetable             telegram_alert.py, notifications.py
```

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # offline: the suite refuses network access
python -m pyflakes *.py sources/ tests/ scripts/
```

CI runs both on every push. Rules for AI agents working here: [AGENTS.md](AGENTS.md).

## License

MIT

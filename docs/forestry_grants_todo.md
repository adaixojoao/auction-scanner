# Forestry grants and inflation: still to do

What shipped: `grants.py` and `data/forest_grants.csv`, and inflation at 3.5% a
year counted against the project in `forestry.py`:
planting costs brought to 2026 prices, income fixed in euros and eroded every
year, the return and the 20% goal after inflation. The items below need the
official texts, which the cloud session could not open (its network policy
refused pepacc.pt, diariodarepublica.pt, agroportal.pt and informador.pt), or a
decision from the owner.

## Needs the official texts

1. **Portugal, C.3.2.1 amounts.** Checked 2026-11-12: Portaria 124/2025/1 Anexo II
   states support rates 75–100% by beneficiary type (kept `rate=0.75` as the
   floor for restantes beneficiários). Aviso 01/C.3.2.1/2025 Anexo I is a
   **schedule of unit costs by operation and slope** (€/ha or €/plant), not one
   `cost_cap_eur_ha`. Rows stay `partial` until slope bands
   (`slope_min`/`slope_max`) model a package. Premiums are C.3.2.8 (separate).
   - <https://diariodarepublica.pt/dr/detalhe/portaria/124-2025-911850388>
   - <https://pepacc.pt/wp-content/uploads/2025/03/Aviso-01-C321-2025.pdf>
   - <https://pepacc.pt/wp-content/uploads/2025/03/Ficha-de-intervencao-C.3.2.1.-%E2%80%93-Florestacao-de-terras-agricolas-e-nao-agricolas.pdf>
2. **Portugal, the indicative maintenance figures** (€125 / €100 a hectare a
   year under 25% slope, €165 / €135 above) come from a 2022 GPP guide and may
   belong to C.3.2.8. Confirm which measure they are for before using them.
3. **Portugal, eucalyptus.** Confirmed 2026-11-12: Portaria 124/2025/1 Anexo I
   excludes fast-growth species (talhadia <20 years, Christmas trees, energy
   crops). `grants.NEVER` already blocks eucalyptus.
4. **Portugal, which species each PROF favours.** `grants.FAVOURED` is cork oak
   and a native mix; the regional plans decide. Map district → PROF region.
5. **Portugal, replanting existing forest** (not C.3.2.1). Find the measure and
   add a `replanting` row.
6. **France, décret n° 2026-937.** Checked 2026-11-12: articles D156-6 to D156-11 say replanting subsidies for full planting follow a national schedule set by ministerial order. They do not state a euro amount. The row stays `partial`. Read the decree and its planting schedule
   annex. Fix the base rate (sources say 37.5–50%), the certification bonus and
   the per-hectare schedule, and check that the 2025 conditions still hold
   (timber worth under 3× the replanting cost, species mix above 4 ha).
   - <https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000025244092/LEGISCTA000026127916/>
7. **Spain, by region.** Measure 6881.1 (planting, agroforestry) and 6883
   (productive investment) per comunidad: at least Galicia, Castilla y León,
   Extremadura, Andalucía. Needs a district → comunidad map and a `region` per row.
8. **Belgium, Netherlands, Luxembourg.** Not researched.
9. **Call deadlines.** Done 2026-11-12: `call_closes` column; PT C.3.2.1 call
   01/C.3.2.1/2025 closed **2025-09-15** (pepacc.pt). A closed call is refused,
   not scored.

## Decided

10. **A confirmed grant reaches the Forestry score** (2026-11-09). A row that
    is `confirmed` and has a per-hectare figure is in the Forestry return as an
    estimate, and only if an application is approved. It does not set a bid and
    it never pays for the land. A partial row is named only. The live table is
    still partial, so scores do not move until the official amounts in items
    1–7 are filled in.

## Needs a decision from the owner

11. **Inflation as a setting.** 3.5% is a constant (`forestry.INFLATION`).
    Move it to Settings (`config.DEFAULTS`) if it should be changed there.
12. **Income that does keep pace.** Today no income rises with prices (the
    cautious case). If a price series shows timber or cork tracking inflation,
    a crop could be given its own rate instead.
13. **Which year is "today".** `forestry.PRICE_YEAR` is fixed at 2026 so the
    figures stay stable. Move it forward each year, or use the current year.

## Small follow-ups

14. Name a partial grant in the Forestry tab's reasons and the investigation
    brief, not only in the details panel. A confirmed figure is already in the
    return line.
15. Bring the other dated costs to today's prices: the plant material in
    `forestry.PLANT_MATERIAL` and the orchard planting costs in `forestry.CROPS`
    are undated, and the 10-year land comparison (`land_prices.py`) does not
    yet count what the money paid loses to inflation.
16. Ask the AI check to look for grant eligibility in the notice (farm or
    non-farm land, ZIF membership).

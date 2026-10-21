# Forestry grants and inflation: still to do

What shipped: `grants.py` and `data/forest_grants.csv` (named, never scored) and
today's-money pricing at 3.5% inflation in `forestry.py`. These need the
official texts, which the cloud session could not open (its network policy
refused pepacc.pt, diariodarepublica.pt, agroportal.pt and informador.pt), or a
decision from the owner.

## Needs the official texts

1. **Portugal, C.3.2.1 amounts.** Read Annex II of Portaria n.º 124/2025/1 (as
   amended by 313/2025/1 and 281-B/2026/1) and the call notice
   Aviso 01/C.3.2.1/2025. Fill `cost_cap_eur_ha` by slope band (needs a
   `slope_min`/`slope_max` pair of columns), `premium_eur_ha_year` for favoured
   broadleaves and for other species, and the income-loss premium. Then mark the
   rows `confirmed`.
   - <https://diariodarepublica.pt/dr/detalhe/portaria/124-2025-911850388>
   - <https://pepacc.pt/wp-content/uploads/2025/03/Aviso-01-C321-2025.pdf>
   - <https://pepacc.pt/wp-content/uploads/2025/03/Ficha-de-intervencao-C.3.2.1.-%E2%80%93-Florestacao-de-terras-agricolas-e-nao-agricolas.pdf>
2. **Portugal, the indicative maintenance figures** (€125 / €100 a hectare a
   year under 25% slope, €165 / €135 above) come from a 2022 GPP guide and may
   belong to C.3.2.8. Confirm which measure they are for before using them.
3. **Portugal, eucalyptus.** Confirm C.3.2.1 excludes it (`grants.NEVER`
   already does, as the repo's rule).
4. **Portugal, which species each PROF favours.** `grants.FAVOURED` is cork oak
   and a native mix; the regional plans decide. Map district → PROF region.
5. **Portugal, replanting existing forest** (not C.3.2.1). Find the measure and
   add a `replanting` row.
6. **France, décret n° 2026-937.** Read the decree and its planting schedule
   annex. Fix the base rate (sources say 37.5–50%), the certification bonus and
   the per-hectare schedule, and check that the 2025 conditions still hold
   (timber worth under 3× the replanting cost, species mix above 4 ha).
   - <https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000025244092/LEGISCTA000026127916/>
7. **Spain, by region.** Measure 6881.1 (planting, agroforestry) and 6883
   (productive investment) per comunidad: at least Galicia, Castilla y León,
   Extremadura, Andalucía. Needs a district → comunidad map and a `region` per row.
8. **Belgium, Netherlands, Luxembourg.** Not researched.
9. **Call deadlines.** Add a `call_closes` column so a closed call says so.

## Needs a decision from the owner

10. **Should a confirmed grant ever reach the score?** Today it never does.
    A small weight, or a separate "with grants" ranking, is one change in
    `scoring._score_forest`.
11. **Inflation as a setting.** 3.5% is a constant (`forestry.INFLATION`).
    Move it to Settings (`config.DEFAULTS`) if it should be changed there.
12. **The 20% goal: today's money or money of the day?** The score compares
    the return in today's money (the stricter reading). The reason shows both.
13. **Which year is "today".** `forestry.PRICE_YEAR` is fixed at 2026 so the
    figures stay stable. Move it forward each year, or use the current year.

## Small follow-ups

14. Show a grant that may apply in the Forestry tab's reasons and the
    investigation brief, not only in the details panel.
15. Bring other dated figures into today's money: the SAFER forest price in
    `land_prices.py` and the undated crop figures in `forestry.CROPS` once
    they are sourced.
16. Ask the AI check to look for grant eligibility in the notice (farm or
    non-farm land, ZIF membership).

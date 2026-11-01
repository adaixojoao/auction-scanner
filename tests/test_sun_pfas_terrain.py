"""Winter sun (terrain horizon and December clouds, PVGIS), PFAS in water
nearby (EEA) and altitude and slope (EU-DEM), 2026-11-01."""
import surroundings


def test_the_hills_take_the_winter_sun():
    flat = surroundings.winter_sun_hours(40.875, -8.674, [(-180.0, 0.0), (180.0, 0.0)])
    assert 9.0 <= flat["day"] <= 9.4 and flat["day"] - flat["sun"] <= 0.2   # sunrise counts from -0.8°
    valley = surroundings.winter_sun_hours(40.875, -8.674, [(-180.0, 0.0), (-60.0, 20.0), (0.0, 25.0),
                                                            (60.0, 20.0), (180.0, 0.0)])
    assert valley["sun"] < 6 and valley["day"] == flat["day"]
    walled = surroundings.winter_sun_hours(46.0, 7.75, [(-180.0, 40.0), (180.0, 40.0)])
    assert walled["sun"] == 0 and walled["clear_dni"] == 0


def test_the_clouds_and_the_note():
    hours = {"day": 9.2, "sun": 9.0, "clear_dni": 4.0}
    found = surroundings.winter_sun_found(hours, [80.0, 88.0], 2005, 2023)      # 84 kWh/m² of 124 clear
    assert found["share"] == 0.68 and found["hours"] == 6.1
    assert surroundings._winter_sun_note(found) == (
        "on 21 December the sun is up 9.2 h: 9.0 h of direct sun on a clear day; December's clouds let through "
        "68% of the direct sun, so about 6.1 h on an average day (PVGIS, JRC: terrain horizon and SARAH-3 "
        "satellite, 2005–2023; buildings and trees not counted)")
    shaded = surroundings.winter_sun_found({"day": 8.6, "sun": 0.0, "clear_dni": 0.0}, [0.0], 2005, 2023)
    line = surroundings._winter_sun_note(shaded)
    assert line.startswith("on 21 December the sun is up 8.6 h, the hills hide it 8.6 h: 0.0 h of direct sun on a "
                           "clear day — under 6 h even on clear winter days")
    assert "clouds" not in line


def _row(site, date, code, value, below=0, water="GW", lat=45.708, lon=4.82):
    return {"monitoringSiteIdentifier": site, "phenomenonTimeSamplingDate": date, "Year": "2023",
            "observedPropertyDeterminandCode": code, "resultObservedValue": value, "resultUom": "ug/L",
            "resultQualityObservedValueBelowLOQ": below, "parameterWaterBodyCategory": water, "lat": lat, "lon": lon}


def test_pfas_summed_per_sample_against_the_drinking_water_limit():
    rows = [_row("A", 1, "PFOA", 2.15), _row("A", 1, "PFHxA", 4.11), _row("A", 1, "PFHxA", 4.11),
            _row("A", 2, "PFOA", 0.5), _row("B", 1, "PFOS", 0.01, below=1, water="RW", lat=45.69)]
    found = surroundings.pfas_found(rows, 45.70, 4.82)
    assert found == {"sites": 2, "worst": {"sum": 6.26, "water": "groundwater", "year": "2023", "km": 0.9}}
    assert surroundings._pfas_note(found).startswith(
        "PFAS in groundwater 0.9 km away: 6.26 µg/l (the PFAS measured, summed) in 2023, 63× the EU drinking-water "
        "limit (0.1 µg/l for 20 PFAS)")
    clean = surroundings.pfas_found(rows[-1:], 45.70, 4.82)
    assert surroundings._pfas_note(clean) == ("PFAS measured at 1 water monitoring site within 3 km, all under the EU "
                                              "drinking-water limit (0.1 µg/l for 20 PFAS) (EEA, WISE)")
    assert surroundings._pfas_note(surroundings.pfas_found([], 45.7, 4.82)) is None


def test_altitude_slope_and_the_way_it_faces():
    #            here  north  south  east   west: falls to the north
    steep = surroundings.terrain_found([307.0, 300.0, 313.0, 307.0, 307.0])
    assert steep == {"m": 307, "slope": 22, "facing": "north"}
    assert surroundings._terrain_note(steep) == ("altitude 307 m, slope 22% facing north (steep: terraces, retaining "
                                                 "walls and access cost more) (EU-DEM 25 m, Copernicus)")
    south_east = surroundings.terrain_found([100.0, 103.0, 97.0, 97.0, 103.0])
    assert south_east["facing"] == "south-east"
    assert surroundings._terrain_note(surroundings.terrain_found([7.0, 7.1, 6.9, 7.0, 7.0])) == (
        "altitude 7 m, flat ground (EU-DEM 25 m, Copernicus)")
    assert surroundings.terrain_found([1.0, None, 1.0, 1.0, 1.0]) is None

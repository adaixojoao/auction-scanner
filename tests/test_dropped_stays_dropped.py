"""A property you dropped stays dropped when it comes back under another id."""
import json

from db import load_listings, set_listing_status


def _shown(db):
    return {it["id"]: it.get("hidden_reason") for it in
            load_listings(db, filters={"countries": []}, include_hidden=True, apply_min_score=False)}


def test_the_same_house_on_another_site_stays_dropped(db, add):
    """2026-11-21: dropped on Licitor, still shown from Vench as a possible match."""
    add("france", "110542", country="FR", title="Une maison d'habitation de plain pied", price=30000,
        concelho="Saint-Jean-de-Monts", date_end="2026-12-04T09:30:00")
    add("vench", "167077", country="FR", title="UNE PROPRIÉTÉ • Saint-Jean-de-Monts", price=30000,
        concelho="Saint-Jean-de-Monts", date_end="2026-12-04")
    db.execute("UPDATE listings SET possible_match = 'france:110542' WHERE id = 'vench:167077'")
    db.commit()
    set_listing_status(db, "france:110542", "dismissed")
    got = _shown(db)
    assert got["vench:167077"] == "dismissed: same property as france:110542"


def test_a_possible_match_with_another_price_or_date_is_not_hidden(db, add):
    add("france", "1", country="FR", title="Maison", price=30000, concelho="Royan", date_end="2026-12-04")
    add("vench", "2", country="FR", title="Maison", price=30400, concelho="Royan", date_end="2026-12-04")
    add("vench", "3", country="FR", title="Maison", price=30000, concelho="Royan", date_end="2027-01-15")
    db.execute("UPDATE listings SET possible_match = 'france:1' WHERE id IN ('vench:2', 'vench:3')")
    db.commit()
    set_listing_status(db, "france:1", "dismissed")
    got = _shown(db)
    assert not (got["vench:2"] or "").startswith("dismissed")
    assert not (got["vench:3"] or "").startswith("dismissed")


def test_the_kept_copy_of_a_dropped_duplicate_stays_dropped(db, add):
    add("eleiloes", "a", title="Moradia T3", price=50000, concelho="Leiria")
    add("citius", "b", title="Moradia T3", price=50000, concelho="Leiria")
    db.execute("UPDATE listings SET duplicate_of = 'citius:b' WHERE id = 'eleiloes:a'")
    db.commit()
    set_listing_status(db, "eleiloes:a", "dismissed")
    assert _shown(db)["citius:b"] == "dismissed: same property as eleiloes:a"


def test_a_new_round_of_the_same_case_and_property_stays_dropped(db, add):
    case = json.dumps({"processo": "1987/24.3T8ANS, Juízo de Ansião"})
    add("eleiloes", "r1", title="T0 - MACEIRA", price=17000, area_m2=48, concelho="Leiria",
        date_end="2026-12-03T10:30:00", raw_json=case)
    add("eleiloes", "r2", title="T0 - MACEIRA", price=12000, area_m2=48, concelho="Leiria",
        date_end="2027-03-03T10:30:00", raw_json=case)
    # Another lot of the same case (a different size) is another property.
    add("eleiloes", "lot", title="T0 - MACEIRA", price=15000, area_m2=325, concelho="Leiria",
        date_end="2026-12-03T10:30:00", raw_json=case)
    set_listing_status(db, "eleiloes:r1", "dismissed")
    got = _shown(db)
    assert got["eleiloes:r2"] == "dismissed: same property as eleiloes:r1"
    assert not (got["eleiloes:lot"] or "").startswith("dismissed")


def test_a_starred_twin_stays_shown(db, add):
    add("eleiloes", "a", title="Moradia T3", price=50000, concelho="Leiria")
    add("citius", "b", title="Moradia T3", price=50000, concelho="Leiria")
    db.execute("UPDATE listings SET duplicate_of = 'citius:b' WHERE id = 'eleiloes:a'")
    db.commit()
    set_listing_status(db, "eleiloes:a", "dismissed")
    set_listing_status(db, "citius:b", "shortlisted")
    assert not (_shown(db)["citius:b"] or "").startswith("dismissed")

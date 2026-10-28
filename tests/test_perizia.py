import perizia

PAD = " Descrizione dell'appartamento, cucina, bagno e due camere, in muratura." * 10


def test_share_and_occupation_are_read_from_the_findings():
    found = perizia.read(PAD + " Comproprietario - Quota: 1/3 - Tipologia del diritto: nuda proprietà. "
                         "11. STATO DI POSSESSO: Occupato dal debitore e dai suoi familiari.")
    assert found["share"] and found["occupied"] and found["lots"] == 1


def test_the_experts_instructions_are_not_findings():
    found = perizia.read(PAD + " 8. in caso di opere abusive, controlli la possibilità di sanatoria; in caso di "
                         "abuso assolutamente non sanabile l'esperto determinerà il valore. 10. determini il valore "
                         "(se trattasi di quota indivisa indichi altresì il suo solo valore)." + PAD + " L'appartamento presenta "
                         "segni diffusi di muffa. VALORE: valore di mercato stimato € 30.600,00")
    assert "share" not in found and "not_legal" not in found
    assert found["damp"] and found["market_value"] == 30600


def test_a_scanned_report_is_kept_as_scanned():
    assert perizia.read("  \n ") == {"scanned": True}


def test_effect_blocks_a_share_and_charges_a_lease():
    block, factor, _ = perizia.effect({"found": True, "lots": 1, "share": "nuda proprietà 1/3"})
    assert block and factor == 1.0
    block, factor, why = perizia.effect({"found": True, "lots": 1, "leased": "locazione opponibile"})
    assert block is None and factor == perizia.LEASED_FACTOR and "lease" in why[0]


def test_effect_of_a_report_on_many_properties_only_asks_to_check():
    block, factor, why = perizia.effect({"found": True, "lots": 19, "not_legal": "non sanabile"})
    assert block is None and factor == 1.0 and "19 properties" in why[0]


def test_effect_without_a_readable_report_is_nothing():
    assert perizia.effect(None) == (None, 1.0, [])
    assert perizia.effect({"found": True, "scanned": True}) == (None, 1.0, [])


def test_title_history_and_checklists_are_not_a_share():
    history = perizia.read(PAD + " Alla sig.ra l'immobile era pervenuto in nuda proprietà per atto di donazione." + PAD)
    checklist = perizia.read(PAD + " L'avviso deve contenere le seguenti indicazioni: DIRITTO IN VENDITA intera "
                             "proprietà; nuda proprietà; usufrutto." + PAD)
    sold = perizia.read(PAD + " L'immobile è pignorato solo per la quota di 1/3 appartenente al debitore." + PAD)
    assert "share" not in history and "share" not in checklist
    assert sold["share"]

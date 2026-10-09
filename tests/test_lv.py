from sources import lv

ROW = ('<tr id="tr_58108523"><td class="msga2 pp0"></td><td class=msg2><div class=d1>'
       '<a data="x" id="dm_1" class="am" href="/msg/lv/real-estate/wood/madona-and-reg/barkavas-pag/exmih.html">'
       'Pārdod meža zemi 21 ha</a></div></td><td class="msga2-o pp6" nowrap c=1>Barkavas pag.<br>Radžēļi</td>'
       '<td class="msga2-o pp6" nowrap c=1>21.5 ha.</td><td class="msga2-o pp6" nowrap c=1>46,000  €</td></tr>')
DETAIL = ('<div id="msg_div_msg">Mežs ar priedēm, ceļš līdz zemei.<br><br><table>'
          '<td class="ads_opt" id="tdo_20" nowrap><b>Madona un raj.</b></td>'
          "onclick=\"mnu('map',1,1,'/lv/gmap/x.html?mode=1&c=56.73016, 26.54564, 15')\""
          '<img src="https://i.ss.lv/gallery/8/1/2/wood-77398688.800.jpg">')


def test_ss_lv_reads_the_forest_table_and_the_ad():
    [row] = lv.parse_ss_rows(ROW)
    assert (row["id"], row["ha"], row["price"], row["place"]) == ("58108523", 21.5, 46000.0, "Barkavas pag., Radžēļi")
    ad = lv.parse_ss_detail(DETAIL)
    assert ad["description"] == "Mežs ar priedēm, ceļš līdz zemei."
    assert (ad["lat"], ad["lon"], ad["district"]) == (56.73016, 26.54564, "Madona un raj.")
    assert ad["photos"] == ["https://i.ss.lv/gallery/8/1/2/wood-77398688.800.jpg"]


HOME = ('<tr id="tr_54685970"><td class="msga2 pp0"></td><td class="msga2">'
        '<a class="am" href="/msg/lv/real-estate/homes-summer-residences/aizkraukle-and-reg/plavinas/efdfe.html">'
        'Privātmāja</a></td><td class="msga2-o pp6">Pļaviņas</td>'
        '<td class="msga2-o pp6">80</td><td class="msga2-o pp6">2</td>'
        '<td class="msga2-o pp6">1206 m²</td><td class="msga2-o pp6">15,000 €</td></tr>')
PLOT = ('<tr id="tr_54685971"><td class="msga2 pp0"></td><td class="msga2">'
        '<a class="am" href="/msg/lv/real-estate/plots-and-lands/aizkraukle-and-reg/plavinas/abc.html">'
        'Zeme</a></td><td class="msga2-o pp6">Pļaviņas</td>'
        '<td class="msga2-o pp6">0.12 ha.</td><td class="msga2-o pp6">7.08 €</td>'
        '<td class="msga2-o pp6">8,490 €</td></tr>'
        '<tr id="tr_54685972"><td class="msga2"><a class="am" href="/msg/x.html">Zeme</a></td>'
        '<td class="msga2-o pp6">Pļaviņas</td><td class="msga2-o pp6">1 ha.</td>'
        '<td class="msga2-o pp6">1 €</td><td class="msga2-o pp6">Cena runājama</td></tr>')


def test_ss_lv_reads_a_house_and_ignores_the_plot_rate():
    [house] = lv.parse_ss_sale_rows(HOME, "house")
    assert (house["id"], house["area_m2"], house["price"], house["place"]) == ("54685970", 80, 15000, "Pļaviņas")
    assert house["url"].endswith("/efdfe.html")
    [plot] = lv.parse_ss_sale_rows(PLOT, "terreno")
    assert plot["id"] == "54685971" and plot["area_m2"] == 1200 and plot["price"] == 8490


def test_safer_reads_a_forest_card():
    from sources import fr
    block = ('<h2 itemprop="name"><a href="/immobilier/vente-foret-landes-fr_VN32211.htm" title="x">Forêt de pins</a></h2>'
             '<p itemprop="description"><strong>Vocations :</strong> Forêt</p>'
             '<a class="safer_region_link" href="/vente-propriete-agricole/nouvelle-aquitaine/landes,40">Landes</a>'
             "<b class='safer_land_value'>23 ha 66 a 25 ca</b><div itemprop=\"price\" content=\"79830\">79 830 €</div>")
    row = fr.parse_safer("VN32211", block)
    assert (row["id"], row["area_m2"], row["price"], row["district"], row["concelho"], row["freguesia"]) == \
        ("safer:VN32211", 236625.0, 79830.0, "40", "Mont-de-Marsan", "Landes")
    assert fr.parse_safer("VN1", block.replace('content="79830"', 'content="0"')) is None   # "Nous consulter"

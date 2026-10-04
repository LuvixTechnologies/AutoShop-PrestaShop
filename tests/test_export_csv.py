import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import export_csv as ex
import import_catalog as ic

DATA = Path(__file__).resolve().parent.parent / "data"

def test_ligne_identique_a_l_export_prestashop():
    # lignes copiées de l'export réel (product_2026-10-04_093001.csv)
    mug = '19;http://localhost:8080/img/p/2/2/22-small_default.jpg;"Mug personnalisable";demo_14;"Accessoires de maison";13.900000;13,90\u00a0€;300'
    carnet = '18;http://localhost:8080/img/p/2/0/20-small_default.jpg;"Carnet de notes Colibri";demo_10;Papeterie;12.900000;12,90\u00a0€;1200'
    assert ex.format_row(19, "http://localhost:8080/img/p/2/2/22-small_default.jpg", "Mug personnalisable",
                         "demo_14", "Accessoires de maison", 13.9, 0, 300).rsplit(";", 2)[0] == mug.rsplit(";", 2)[0]
    assert ex.format_row(18, "http://localhost:8080/img/p/2/0/20-small_default.jpg", "Carnet de notes Colibri",
                         "demo_10", "Papeterie", 12.9, 0, 1200) == carnet

def test_entete_et_tri():
    lines = ex.build_lines(ic.load_catalog(DATA), start_id=20, tva=20)
    assert lines[0] == '"Product ID";Image;Nom;Référence;Catégorie;"Montant HT";"Montant TTC";Quantité'
    assert len(lines) == 31
    ids = [int(l.split(";")[0]) for l in lines[1:]]
    assert ids == sorted(ids, reverse=True) and ids[0] == 49 and ids[-1] == 20

def test_ttc_arrondi():
    assert ex.format_ttc(62.50, 20) == "75,00\u00a0€"
    assert ex.format_ttc(19.90, 20) == "23,88\u00a0€"
    assert ex.format_ht(19.9) == "19.900000"

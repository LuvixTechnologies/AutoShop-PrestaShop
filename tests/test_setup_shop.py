import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import xml.etree.ElementTree as ET
import setup_shop as ss

PRODUCTS = [{"id": 1, "reference": "demo_1"}, {"id": 2, "reference": "demo_2"}, {"id": 30, "reference": "FRE-001"}]
CATS = [
    {"id": 1, "id_parent": 0, "level_depth": 0, "name": [{"id": 1, "value": "Racine"}]},
    {"id": 2, "id_parent": 1, "level_depth": 1, "name": [{"id": 1, "value": "Accueil"}]},
    {"id": 3, "id_parent": 2, "level_depth": 2, "name": [{"id": 1, "value": "Clothes"}]},
    {"id": 4, "id_parent": 3, "level_depth": 3, "name": [{"id": 1, "value": "Men"}]},
    {"id": 9, "id_parent": 2, "level_depth": 2, "name": "Freinage"},
]
BRANDS = [{"id": 1, "name": "Studio Design"}]

def test_plan_demo_protege_racine_accueil_et_nos_categories():
    plan = ss.plan_cleanup(PRODUCTS, CATS, BRANDS, {"Freinage"}, "demo")
    assert plan["products"] == [1, 2]                       # on garde FRE-001
    ids = [c[0] for c in plan["categories"]]
    assert ids == [4, 3]                                    # enfants avant parents
    assert 1 not in ids and 2 not in ids and 9 not in ids
    assert plan["manufacturers"] == [(1, "Studio Design")]

def test_plan_all_supprime_tous_les_produits():
    assert ss.plan_cleanup(PRODUCTS, CATS, BRANDS, set(), "all")["products"] == [1, 2, 30]

class Fake:
    def __init__(self): self.deleted = []; self.puts = []
    def delete(self, path): self.deleted.append(path)
    def find_id(self, resource, filters): return 7 if filters["name"] != "PS_SHOP_PHONE" else None
    def get_xml(self, path): return ET.fromstring("<prestashop><configuration><value/><date_add>0000-00-00 00:00:00</date_add><date_upd/></configuration></prestashop>")
    def put(self, path, root):
        assert root.find("configuration/date_add") is None and root.find("configuration/date_upd") is None
        self.puts.append((path, root.find("configuration/value").text))

def test_simulation_ne_supprime_rien_et_apply_supprime():
    plan = ss.plan_cleanup(PRODUCTS, CATS, BRANDS, {"Freinage"}, "demo")
    f = Fake(); ss.run_cleanup(f, plan, apply=False, log=lambda *_: None)
    assert f.deleted == []
    ss.run_cleanup(f, plan, apply=True, log=lambda *_: None)
    assert f.deleted == ["products/1", "products/2", "categories/4", "categories/3", "manufacturers/1"]

def test_identite():
    f = Fake(); logs = []
    ss.apply_identity(f, {"shop_name": "Ma boutique", "email": "a@b.fr", "phone": "0102"}, logs.append)
    assert ("configurations/7", "Ma boutique") in f.puts
    assert any("PS_SHOP_PHONE introuvable" in l for l in logs)

def test_logo_genere(tmp_path):
    logo, ico = ss.make_logo_files("JDM GARAGE", tmp_path)
    assert logo is None or (logo.exists() and ico.exists())

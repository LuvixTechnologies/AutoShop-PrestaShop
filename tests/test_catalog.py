import shutil, sys, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import import_catalog as ic

DATA = Path(__file__).resolve().parent.parent / "data"

def test_catalog_valide():
    cat = ic.load_catalog(DATA)
    assert not cat.errors
    assert len(cat.parts) == 30 and len(cat.engines) == 6 and len(cat.vehicles) == 3
    assert any("HAB-004" in w for w in cat.warnings)      # compat vide
    assert any("HAB-003" in w for w in cat.warnings)      # description courte

def test_expansion_compat():
    cat = ic.load_catalog(DATA)
    p = {x.sku: x for x in cat.parts}
    assert p["FRE-001"].engines == {"CEL-3SGE", "CEL-3SGTE"}
    assert p["FRE-005"].engines == set(cat.engines)       # ALL
    assert p["ADM-004"].engines == {"PRE-F20B"}

def test_erreurs_detectees(tmp_path):
    d = tmp_path / "d"; shutil.copytree(DATA, d)
    with open(d / "parts.csv", "a", encoding="utf-8") as f:
        f.write("FRE-001,Doublon,X,Freinage,10,1,ALL,Description assez longue pour passer la validation.\n")
        f.write("ZZZ-001,Mauvais moteur,X,Freinage,10,1,ENG:INCONNU,Description assez longue pour passer la validation.\n")
        f.write("ZZZ-002,Prix faux,X,Freinage,abc,1,ALL,Description assez longue pour passer la validation.\n")
        f.write("ZZZ-003,Token faux,X,Freinage,10,1,FOO:1,Description assez longue pour passer la validation.\n")
    cat = ic.load_catalog(d)
    joined = " ".join(cat.errors)
    assert "en double" in joined and "moteur inconnu" in joined and "prix invalide" in joined and "invalide '" in joined

def test_contenu_suspect(tmp_path):
    d = tmp_path / "d"; shutil.copytree(DATA, d)
    with open(d / "parts.csv", "a", encoding="utf-8") as f:
        f.write("ZZZ-010,=HYPERLINK(\"http://x\"),X,Freinage,10,1,ALL,Description assez longue pour passer la validation.\n")
        f.write("ZZZ-011,Piece,X,Freinage,10,1,ALL,<script>alert(1)</script> Description assez longue pour passer.\n")
    cat = ic.load_catalog(d)
    assert sum("contenu suspect" in e for e in cat.errors) == 2

def test_description_echappee():
    cat = ic.load_catalog(DATA); cat.parts[0].description = "A <b>x</b> & y"
    fake = FakeClient(); captured = []
    orig = fake.create
    fake.create = lambda r, root: (captured.append(ET.tostring(root, encoding="unicode")) or orig(r, root))
    imp = ic.Importer(fake, cat, log=lambda *_: None); imp.langs = [1]
    imp.create_product(cat.parts[0])
    assert "&lt;b&gt;" in captured[-1] or "&amp;lt;b&amp;gt;" in captured[-1]

def test_dry_run_ecrit_rapport(tmp_path):
    rc = ic.main(["--data-dir", str(DATA), "--report", str(tmp_path / "r.md")])
    assert rc == 0 and (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Rapport")

class FakeClient:
    """Simule le webservice : mémorise les ressources créées."""
    BLANK = {
        "categories": "<prestashop><category><id_parent/><active/><level_depth/><name><language id='1'/></name><link_rewrite><language id='1'/></link_rewrite><associations><categories/></associations></category></prestashop>",
        "product_features": "<prestashop><product_feature><name><language id='1'/></name></product_feature></prestashop>",
        "product_feature_values": "<prestashop><product_feature_value><id_feature/><custom/><value><language id='1'/></value></product_feature_value></prestashop>",
        "products": "<prestashop><product><manufacturer_name/><quantity/><reference/><price/><name><language id='1'/></name><link_rewrite><language id='1'/></link_rewrite><description_short><language id='1'/></description_short><description><language id='1'/></description><associations><images/><categories/><product_features/></associations></product></prestashop>",
    }
    def __init__(self):
        self.store = {}; self.n = 0; self.puts = []
    def language_ids(self): return [1, 2]
    def find_id(self, resource, filters):
        for rid, (res, attrs) in self.store.items():
            if res == resource and all(attrs.get(k) == str(v) for k, v in filters.items()):
                return rid
        if resource == "stock_availables":
            return 1000 + int(filters["id_product"])
        return None
    def schema(self, resource): return ET.fromstring(self.BLANK[resource])
    def create(self, resource, root):
        self.n += 1
        el = root[0]
        attrs = {}
        for tag in ("reference", "id_feature"):
            n = el.find(tag)
            if n is not None: attrs[tag] = n.text
        for tag in ("name", "value"):
            n = el.find(tag)
            if n is not None and len(n): attrs[tag] = n[0].text
        self.store[self.n] = (resource, attrs)
        if resource == "products":
            assert el.find("quantity") is None and el.find("manufacturer_name") is None
            assert el.find("associations/images") is None
            assert len(el.findall("associations/product_features/product_feature")) >= 1
        return self.n
    def get_xml(self, path): return ET.fromstring("<prestashop><stock_available><quantity/><depends_on_stock/></stock_available></prestashop>")
    def put(self, path, root): self.puts.append((path, root.find("stock_available/quantity").text))

def test_import_complet_et_idempotent():
    cat = ic.load_catalog(DATA)
    fake = FakeClient()
    logs = []
    stats = ic.Importer(fake, cat, log=logs.append).run()
    assert stats["créés"] == 30 and len(fake.puts) == 30
    products = [r for r, _ in fake.store.values() if r == "products"]
    assert len(products) == 30
    assert sum(1 for r, _ in fake.store.values() if r == "categories") == 7
    assert sum(1 for r, a in fake.store.values() if r == "product_features") == 3
    stats2 = ic.Importer(fake, cat, log=logs.append).run()
    assert stats2["créés"] == 0 and stats2["ignorés (déjà présents)"] == 30

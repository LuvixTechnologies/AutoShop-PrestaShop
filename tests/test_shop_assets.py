import sys
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import import_catalog as ic
import shop_assets as sa

DATA = Path(__file__).resolve().parent.parent / "data"

def test_find_photos(tmp_path):
    for n in ("FRE-001.jpg", "FRE-001_2.png", "FRE-0011.jpg", "SUS-001.JPG", "notes.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert [p.name for p in sa.find_photos(tmp_path, "FRE-001")] == ["FRE-001.jpg", "FRE-001_2.png"]
    assert [p.name for p in sa.find_photos(tmp_path, "SUS-001")] == ["SUS-001.JPG"]
    assert sa.find_photos(tmp_path / "absent", "X") == []

def test_placeholder(tmp_path):
    from PIL import Image
    part = ic.load_catalog(DATA).parts[0]
    out = sa.make_placeholder(part, tmp_path / "p.jpg")
    img = Image.open(out)
    assert img.size == (1000, 1000) and img.format == "JPEG"

class FakeClient:
    def __init__(self, with_images=()):
        self.uploads, self.with_images = [], set(with_images)
    def find_id(self, resource, filters):
        return {"FRE-001": 1, "FRE-002": 2}.get(filters.get("reference"), None) if resource == "products" else 7
    def get_xml(self, path):
        pid = int(path.rsplit("/", 1)[1])
        return ET.fromstring("<prestashop><image>" + ("<declination id='5'/>" if pid in self.with_images else "") + "</image></prestashop>")
    def upload_image(self, path, f): self.uploads.append((path, Path(f).name))
    def put(self, *a): self.configs = getattr(self, "configs", []) + [a]

def test_upload_photos_substitution_et_idempotence(tmp_path):
    cat = ic.load_catalog(DATA); cat.parts = cat.parts[:3]       # FRE-001, FRE-002, FRE-003 (inconnu de la fausse API)
    (tmp_path / "FRE-001.jpg").write_bytes(b"x")
    c = FakeClient(with_images={2})
    logs = []
    stats = sa.upload_product_images(c, cat, tmp_path, log=logs.append)
    assert c.uploads == [("images/products/1", "FRE-001.jpg")]
    assert stats["photos"] == 1 and stats["déjà présentes"] == 1
    assert any("FRE-003 introuvable" in l for l in logs)
    c2 = FakeClient(); cat.parts = cat.parts[:1]
    assert sa.upload_product_images(c2, cat, tmp_path / "vide", log=lambda *_: None)["substitution"] == 1

def test_disable_modules_en_www_data():
    calls = []
    def runner(cmd): calls.append(cmd); return 0, ""
    sa.disable_home_modules(runner, ["ps_banner", "ps_imageslider"], log=lambda *_: None)
    assert calls[0] == ["docker", "compose", "exec", "-T", "-u", "www-data", "prestashop",
                        "php", "bin/console", "prestashop:module", "disable", "ps_banner"]
    assert calls[-1][-2:] == ["bin/console", "cache:clear"] and len(calls) == 3

def test_install_logo(tmp_path):
    calls = []
    def runner(cmd): calls.append(cmd); return 0, ""
    class C:
        def __init__(self): self.puts = []
        def find_id(self, r, f): return 3
        def get_xml(self, p): return ET.fromstring("<prestashop><configuration><value/></configuration></prestashop>")
        def put(self, p, root): self.puts.append(root.find("configuration/value").text)
    c = C(); logo = tmp_path / "logo.png"; logo.write_bytes(b"x"); ico = tmp_path / "f.ico"; ico.write_bytes(b"x")
    sa.install_logo(c, logo, ico, runner, log=lambda *_: None)
    assert sum(1 for x in calls if x[:3] == ["docker", "compose", "cp"]) == 2
    assert len(c.puts) == 4 and c.puts[0].startswith("logo-") and c.puts[-1].endswith(".ico")

def test_echec_docker_leve_erreur(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        sa.compose_cp(lambda cmd: (1, "boom"), tmp_path / "x.png", "x.png")

def test_upload_image_envoie_le_type_mime(tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    sent = {}
    class Sess:
        def post(self, url, files=None, timeout=None):
            sent.update(files=files); class_ = type("R", (), {"status_code": 200, "text": ""}); return class_()
    c = ic.PrestaClient("http://x", "K"); c.session = Sess()
    c.upload_image("images/products/1", f)
    assert sent["files"]["image"][0] == "a.jpg" and sent["files"]["image"][2] == "image/jpeg"

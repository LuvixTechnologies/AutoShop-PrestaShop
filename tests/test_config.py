import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

def test_parse_env():
    txt = "# commentaire\n\nPS_PORT=8081\nexport DB_PASSWD='a b'\nADMIN_MAIL=\"x@y.fr\"\nsans_egal\n"
    assert config.parse_env(txt) == {"PS_PORT": "8081", "DB_PASSWD": "a b", "ADMIN_MAIL": "x@y.fr"}

def test_load_env_ne_remplace_pas_l_environnement(tmp_path):
    f = tmp_path / ".env"; f.write_text("PS_PORT=9000\nDB_NAME=boutique\n")
    environ = {"PS_PORT": "7000"}
    config.load_env(f, environ)
    assert environ == {"PS_PORT": "7000", "DB_NAME": "boutique"}

def test_update_env_remplace_ou_ajoute(tmp_path):
    f = tmp_path / ".env"; f.write_text("A=1\nPS_API_KEY=\n")
    config.update_env("PS_API_KEY", "XYZ", f); config.update_env("B", "2", f)
    assert config.parse_env(f.read_text()) == {"A": "1", "PS_API_KEY": "XYZ", "B": "2"}

def test_urls():
    assert config.shop_url({}) == "http://localhost:8080"
    assert config.shop_url({"PS_PORT": "8081"}) == "http://localhost:8081"
    assert config.admin_url({"PS_PORT": "8081", "PS_FOLDER_ADMIN": "bo"}) == "http://localhost:8081/bo"
    assert config.shop_url({"PS_URL": "http://boutique.test"}) == "http://boutique.test"

def test_identite_env_prioritaire():
    base = {"shop_name": "Ancien", "city": "Nevers"}
    merged = config.merge_identity(base, {"SHOP_NAME": " Nouveau ", "SHOP_EMAIL": "a@b.fr", "SHOP_CITY": ""})
    assert merged == {"shop_name": "Nouveau", "city": "Nevers", "email": "a@b.fr"}

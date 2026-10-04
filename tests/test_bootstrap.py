import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
import bootstrap as bs

def test_cle_generee_puis_relue_depuis_env(tmp_path):
    env_file = tmp_path / ".env"; env_file.write_text("PS_PORT=8081\n")
    environ = {}
    k1 = bs.get_or_create_key(environ, env_file)
    assert re.fullmatch(r"[A-Z0-9]{32}", k1) and f"PS_API_KEY={k1}" in env_file.read_text()
    assert "PS_PORT=8081" in env_file.read_text()
    assert bs.get_or_create_key({"PS_API_KEY": k1}, env_file) == k1

def test_cle_invalide_refusee(tmp_path):
    with pytest.raises(ValueError):
        bs.get_or_create_key({"PS_API_KEY": "trop-court"}, tmp_path / ".env")

def test_sql_contient_droits_et_est_idempotent():
    key = "A" * 32
    sql = bs.build_api_sql(key)
    assert "PS_WEBSERVICE" in sql and "NOT EXISTS" in sql and "DELETE FROM ps_webservice_permission" in sql
    assert sql.count("'products','GET',@acc") == 1
    assert sql.count("@acc)") >= len(bs.RESOURCES) * len(bs.METHODS)

def test_sql_refuse_les_entrees_dangereuses():
    with pytest.raises(ValueError): bs.build_api_sql("a'; DROP TABLE x;--")
    with pytest.raises(ValueError): bs.build_api_sql("A" * 32, prefix="ps_; DROP")
    with pytest.raises(ValueError): bs.build_revoke_delete_sql("x")

def test_revoke_delete():
    assert "method='DELETE'" in bs.build_revoke_delete_sql("B" * 32)

def test_detect_tax_group():
    groups = [{"id": 1, "name": "US-AL Rate (4%)", "active": 1},
              {"id": 9, "name": "FR Taux standard (20%)", "active": 1},
              {"id": 10, "name": "FR Taux réduit (5.5%)", "active": 1}]
    assert bs.detect_tax_group(groups) == 9
    assert bs.detect_tax_group([{"id": 3, "name": "UK 20%", "active": 1}]) == 3
    assert bs.detect_tax_group([{"id": 4, "name": "X 5%", "active": 1}]) is None
    assert bs.detect_tax_group([{"id": 9, "name": "FR 20%", "active": 0}]) is None

def test_wait_for_shop():
    codes = iter([502, 503, 200]); calls = []
    def get(_):
        c = next(codes)
        return c
    assert bs.wait_for_shop("http://x", timeout=100, interval=0, get=get, sleep=lambda s: None, log=calls.append)
    def boom(_): raise ConnectionError()
    assert not bs.wait_for_shop("http://x", timeout=0, interval=0, get=boom, sleep=lambda s: None, log=lambda *_: None)

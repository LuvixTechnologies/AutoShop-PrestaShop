#!/usr/bin/env python3
"""
Installe et peuple la boutique de démonstration en une commande :

  1. docker compose up        (PrestaShop + MySQL, installation automatique)
  2. création d'une clé API   (écrite en base, puis enregistrée dans le .env)
  3. nettoyage de la démo     (setup_shop.py : produits, catégories, marques, identité)
  4. import du catalogue      (import_catalog.py : data/*.csv -> produits, filtres, stock)
  5. habillage                (shop_assets.py : images produits, logo, accueil sans promo de démo)

Usage :
  python bootstrap.py                # (configuration dans .env) installe (si besoin), puis nettoie et importe
  python bootstrap.py --reset        # repart de zéro (supprime la base et les volumes Docker)
  python bootstrap.py --skip-install # la boutique tourne déjà : seulement clé API + nettoyage + import
Prérequis : Docker Desktop (avec « docker compose »), Python 3.9+, pip install -r requirements.txt
"""
import argparse
import os
import re
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path

import config
import import_catalog as ic
import shop_assets as sa
import setup_shop as ss

RESOURCES = ["categories", "languages", "product_features", "product_feature_values", "products",
             "stock_availables", "manufacturers", "configurations", "tax_rule_groups", "images"]
METHODS = ["GET", "POST", "PUT", "DELETE"]


# --------------------------------------------------------------------------- #
# Fonctions pures (testées)
# --------------------------------------------------------------------------- #
def new_api_key():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(32))


def get_or_create_key(environ=None, env_path=None):
    """Clé API depuis PS_API_KEY (.env) ; sinon on en génère une et on l'écrit dans le .env."""
    environ = os.environ if environ is None else environ
    key = environ.get("PS_API_KEY", "").strip()
    if key:
        if not re.fullmatch(r"[A-Z0-9]{32}", key):
            raise ValueError("PS_API_KEY invalide dans le .env : 32 caractères A-Z et 0-9 attendus (ou laisse vide).")
        return key
    key = new_api_key()
    config.update_env("PS_API_KEY", key, env_path)
    environ["PS_API_KEY"] = key
    return key


def build_api_sql(key, prefix="ps_", description="jdm-garage bootstrap", methods=METHODS):
    """SQL idempotent : active le webservice, crée la clé et ses droits."""
    if not re.fullmatch(r"[A-Z0-9]{32}", key):
        raise ValueError("clé API invalide (32 caractères A-Z0-9 attendus)")
    if not re.fullmatch(r"[a-z0-9_]+", prefix):
        raise ValueError("préfixe de tables invalide")
    acc, shop, perm, conf = (f"{prefix}{t}" for t in
                             ("webservice_account", "webservice_account_shop",
                              "webservice_permission", "configuration"))
    values = ",\n  ".join(f"('{r}','{m}',@acc)" for r in RESOURCES for m in methods)
    return f"""
UPDATE {conf} SET value='1' WHERE name='PS_WEBSERVICE';
INSERT INTO {acc} (`key`, description, class_name, is_module, module_name, active)
  SELECT '{key}', '{description}', 'WebserviceRequest', 0, '', 1 FROM DUAL
  WHERE NOT EXISTS (SELECT 1 FROM {acc} WHERE `key`='{key}');
SET @acc := (SELECT id_webservice_account FROM {acc} WHERE `key`='{key}' LIMIT 1);
INSERT INTO {shop} (id_webservice_account, id_shop)
  SELECT @acc, 1 FROM DUAL
  WHERE NOT EXISTS (SELECT 1 FROM {shop} WHERE id_webservice_account=@acc AND id_shop=1);
DELETE FROM {perm} WHERE id_webservice_account=@acc;
INSERT INTO {perm} (resource, method, id_webservice_account) VALUES
  {values};
"""


def build_revoke_delete_sql(key, prefix="ps_"):
    if not re.fullmatch(r"[A-Z0-9]{32}", key):
        raise ValueError("clé API invalide")
    return (f"DELETE p FROM {prefix}webservice_permission p JOIN {prefix}webservice_account a "
            f"ON a.id_webservice_account=p.id_webservice_account "
            f"WHERE a.`key`='{key}' AND p.method='DELETE';\n")


def detect_tax_group(groups):
    """Choisit le groupe de TVA français à 20 % parmi ceux renvoyés par l'API."""
    def name(g):
        return str(g.get("name", ""))
    active = [g for g in groups if str(g.get("active", "1")) in ("1", "True", "true")]
    for cond in (lambda n: "20" in n and ("FR" in n.upper() or "FRANCE" in n.upper()),
                 lambda n: "20" in n):
        for g in active:
            if cond(name(g)):
                return int(g["id"])
    return None


def wait_for_shop(url, timeout=900, interval=10, get=None, sleep=time.sleep, log=print):
    """Attend que la boutique réponde (Apache ne démarre qu'après l'installation automatique)."""
    if get is None:
        import requests
        get = lambda u: requests.get(u, timeout=10, allow_redirects=False).status_code
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = get(url)
            if status < 500:
                return True
        except Exception:
            pass
        log("  ... installation en cours, attente")
        sleep(interval)
    return False


# --------------------------------------------------------------------------- #
# Exécution
# --------------------------------------------------------------------------- #
def sh(cmd, input_text=None, check=True):
    res = subprocess.run(cmd, input=input_text, text=True, capture_output=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"Commande échouée : {' '.join(cmd)}\n{res.stdout}\n{res.stderr}")
    return res


def run_sql(sql):
    return sh(["docker", "compose", "exec", "-T", "db", "mysql", "-uroot",
               f"-p{config.get('DB_PASSWD')}", config.get("DB_NAME")], input_text=sql)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Installe et peuple la boutique de démonstration")
    ap.add_argument("--reset", action="store_true", help="supprime les volumes Docker avant de relancer")
    ap.add_argument("--skip-install", action="store_true", help="ne touche pas à Docker")
    ap.add_argument("--skip-clean", action="store_true")
    ap.add_argument("--skip-import", action="store_true")
    ap.add_argument("--skip-assets", action="store_true", help="ni images, ni logo, ni accueil")
    ap.add_argument("--keep-delete", action="store_true",
                    help="garde le droit DELETE sur la clé API (retiré par défaut en fin de script)")
    ap.add_argument("--timeout", type=int, default=900, help="attente max de l'installation (secondes)")
    args = ap.parse_args(argv)

    config.load_env()
    shop_url, admin_url, prefix = config.shop_url(), config.admin_url(), config.get("DB_PREFIX")
    if not Path(".env").exists():
        print("Pas de fichier .env : valeurs par défaut utilisées (copie .env en .env pour les changer).")
    elif config.get("ADMIN_PASSWD") == config.DEFAULTS["ADMIN_PASSWD"]:
        print("Attention : mot de passe administrateur par défaut. Change ADMIN_PASSWD dans le .env.")

    # 1. Docker
    if not args.skip_install:
        if args.reset:
            print("[1/6] Réinitialisation des volumes Docker...")
            sh(["docker", "compose", "down", "-v"])
        print("[1/6] Démarrage de PrestaShop (docker compose up)...")
        sh(["docker", "compose", "up", "-d"])
    if not wait_for_shop(shop_url, timeout=args.timeout):
        print("La boutique ne répond pas. Regarde les logs : docker compose logs --tail=60 prestashop")
        return 1
    print(f"[2/6] Boutique en ligne : {shop_url}")

    # 2. Clé API
    try:
        key = get_or_create_key()
    except ValueError as exc:
        print(exc)
        return 1
    print("[3/6] Création de la clé API en base...")
    try:
        run_sql(build_api_sql(key, prefix=prefix))
    except RuntimeError as exc:
        print(exc)
        print("Si la table webservice_account est introuvable, l'installation n'est pas terminée :"
              " réessaie dans une minute, ou crée la clé à la main (README).")
        return 1
    client = ic.PrestaClient(shop_url, key)
    try:
        client.language_ids()
    except Exception as exc:
        print("L'API ne répond pas avec la clé créée :\n", str(exc)[:500])
        print("Vérifie dans le back-office : Paramètres avancés > Webservice (activé ?),"
              " et que /api répond (réécriture d'URL).")
        return 1
    print("      API opérationnelle.")

    # 3. Nettoyage + identité
    if not args.skip_clean:
        print("[4/6] Nettoyage de la démo et identité de la boutique...")
        if ss.main(["--apply"]) != 0:
            return 1

    # 4. Import
    if not args.skip_import:
        print("[5/6] Import du catalogue...")
        tax = detect_tax_group(client.list_json("tax_rule_groups", "id,name,active"))
        print(f"      Groupe de TVA détecté : {tax if tax is not None else 'aucun (à régler dans le back-office)'}")
        import_args = ["--import"] + (["--tax-rules-group", str(tax)] if tax is not None else [])
        if ic.main(import_args) != 0:
            return 1
        expected = len(ic.load_catalog("data").parts)
        found = len([p for p in client.list_json("products", "id,reference")
                     if not str(p.get("reference", "")).startswith("demo_")])
        print(f"      Produits dans la boutique : {found} (attendus : {expected})")

    # 5. Images, logo, accueil
    if not args.skip_assets:
        print("[6/6] Images produits, logo et accueil...")
        if sa.main([]) != 0:
            print("Les images/logo/accueil ont échoué (la boutique reste utilisable) : relance python shop_assets.py")

    # 5. Sécurité : on retire DELETE de la clé
    if not args.keep_delete:
        run_sql(build_revoke_delete_sql(key, prefix=prefix))
        print("Droit DELETE retiré de la clé API.")

    print("\nTerminé.")
    print(f"  Boutique    : {shop_url}")
    print(f"  Back-office : {admin_url}")
    print(f"  Connexion   : {config.get('ADMIN_MAIL')} (mot de passe : ADMIN_PASSWD dans le .env)")
    print("  Clé API     : PS_API_KEY dans le .env (ne la partage pas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

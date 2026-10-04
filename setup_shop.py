#!/usr/bin/env python3
"""
Prépare une boutique PrestaShop de démonstration :
  1. Nettoie les données de démo (produits, catégories, marques).
  2. Applique l'identité de la boutique (nom, coordonnées, logo, favicon).
  3. Affiche les groupes de taxes disponibles (pour --tax-rules-group à l'import).

Par défaut : simulation, rien n'est modifié. Ajoute --apply pour exécuter.

Usage :
  python setup_shop.py                      # simulation
  python setup_shop.py --apply              # nettoyage de la démo + identité
  python setup_shop.py --apply --only clean # seulement le nettoyage
  python setup_shop.py --apply --scope all  # supprime TOUS les produits (dangereux)
Configuration : fichier .env.example (PS_API_KEY, SHOP_*).
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import config
import import_catalog as ic

PROTECTED_CATEGORY_IDS = {1, 2}          # Racine et Accueil : jamais supprimées
DEMO_REFERENCE_PREFIX = "demo_"          # références des produits de démo PrestaShop


def ml_text(value):
    """Valeur d'un champ multilingue renvoyée par l'API (liste de dicts ou chaîne)."""
    if isinstance(value, list):
        return str(value[0].get("value", "")) if value else ""
    return str(value or "")


def plan_cleanup(products, categories, manufacturers, keep_category_names, scope="demo"):
    """Calcule ce qui serait supprimé. Fonction pure, facile à tester."""
    if scope == "all":
        product_ids = [int(p["id"]) for p in products]
    else:
        product_ids = [int(p["id"]) for p in products
                       if str(p.get("reference", "")).startswith(DEMO_REFERENCE_PREFIX)]
    keep = {n.lower() for n in keep_category_names}
    cats = [c for c in categories
            if int(c["id"]) not in PROTECTED_CATEGORY_IDS and ml_text(c.get("name")).lower() not in keep]
    # les plus profondes d'abord, pour supprimer les enfants avant les parents
    cats.sort(key=lambda c: int(c.get("level_depth", 0)), reverse=True)
    return {
        "products": product_ids,
        "categories": [(int(c["id"]), ml_text(c.get("name"))) for c in cats],
        "manufacturers": [(int(m["id"]), str(m.get("name", ""))) for m in manufacturers],
    }


def run_cleanup(client, plan, apply, log=print):
    log(f"Produits à supprimer : {len(plan['products'])}")
    log("Catégories à supprimer : " + (", ".join(n for _, n in plan["categories"]) or "aucune"))
    log("Marques à supprimer : " + (", ".join(n for _, n in plan["manufacturers"]) or "aucune"))
    if not apply:
        return
    for pid in plan["products"]:
        client.delete(f"products/{pid}")
    for cid, _ in plan["categories"]:
        client.delete(f"categories/{cid}")
    for mid, _ in plan["manufacturers"]:
        client.delete(f"manufacturers/{mid}")
    log("Nettoyage terminé.")


SHOP_CONFIG_KEYS = {
    "shop_name": "PS_SHOP_NAME", "email": "PS_SHOP_EMAIL", "phone": "PS_SHOP_PHONE",
    "address": "PS_SHOP_ADDR1", "postcode": "PS_SHOP_CODE", "city": "PS_SHOP_CITY",
}


def set_configuration(client, name, value):
    cid = client.find_id("configurations", {"name": name})
    if cid is None:
        return False
    root = client.get_xml(f"configurations/{cid}")
    conf = root.find("configuration")
    # l'API renvoie parfois des dates vides/invalides qu'elle refuse ensuite à l'écriture
    ic.remove_tags(conf, ["date_add", "date_upd"])
    ic.set_text(conf, "value", value)
    client.put(f"configurations/{cid}", root)
    return True


def apply_identity(client, settings, log=print):
    for key, conf in SHOP_CONFIG_KEYS.items():
        value = (settings.get(key) or "").strip()
        if not value:
            continue
        ok = set_configuration(client, conf, value)
        log(f"  {conf} = {value!r}" if ok else f"  ! {conf} introuvable, à régler dans le back-office")


def make_logo_files(text, out_dir):
    """Génère un logo texte (PNG) et un favicon (ICO). Retourne (logo, favicon) ou (None, None)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None, None

    def font(size):
        for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default(size)

    logo = Image.new("RGBA", (520, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(logo)
    d.text((10, 20), text, font=font(64), fill=(20, 20, 20, 255))
    d.rectangle((10, 100, 510, 108), fill=(200, 30, 30, 255))
    logo_path = Path(out_dir) / "logo.png"
    logo.save(logo_path)

    icon = Image.new("RGBA", (64, 64), (200, 30, 30, 255))
    ImageDraw.Draw(icon).text((14, 4), text[:1], font=font(52), fill=(255, 255, 255, 255))
    ico_path = Path(out_dir) / "favicon.ico"
    icon.save(ico_path, sizes=[(32, 32), (64, 64)])
    return logo_path, ico_path


def upload_logos(client, text, custom_logo=None, log=print):
    with tempfile.TemporaryDirectory() as tmp:
        logo, ico = make_logo_files(text, tmp)
        if custom_logo:
            logo = Path(custom_logo)
        if logo is None:
            log("  Logo ignoré : installe Pillow (pip install pillow) ou fournis --logo fichier.png")
            return
        for label, path, target in (("logo", logo, "images/general/header"),
                                    ("favicon", ico, "images/general/store_icon")):
            if path is None:
                continue
            try:
                client.upload_image(target, path)
                log(f"  {label} envoyé")
            except ic.PSError as exc:
                log(f"  ! {label} non envoyé via l'API (à faire dans Design > Thème & logo) :\n    "
                    + str(exc).splitlines()[0])


def show_tax_groups(client, log=print):
    log("Groupes de règles de taxe (pour --tax-rules-group à l'import) :")
    for g in client.list_json("tax_rule_groups", "id,name,active"):
        log(f"  id {g['id']} : {g.get('name')} (actif : {g.get('active')})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nettoyage et identité d'une boutique PrestaShop de démo")
    ap.add_argument("--apply", action="store_true", help="exécute réellement les modifications")
    ap.add_argument("--only", choices=["clean", "identity"], help="limite à une seule étape")
    ap.add_argument("--scope", choices=["demo", "all"], default="demo",
                    help="demo : produits dont la référence commence par 'demo_' ; all : tous les produits")
    ap.add_argument("--shop", default="shop.json", help="fichier JSON d'identité de la boutique")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--logo", help="logo personnalisé (PNG) à la place du logo généré")
    args = ap.parse_args(argv)

    config.load_env()
    url, key = config.shop_url(), os.environ.get("PS_API_KEY")
    if not key:
        print("PS_API_KEY manquante : renseigne-la dans le .env.example (ou lance bootstrap.py, qui la crée).")
        return 2
    client = ic.PrestaClient(url, key)
    if not args.apply:
        print("MODE SIMULATION : rien n'est modifié. Ajoute --apply pour exécuter.\n")
    if args.scope == "all" and args.apply:
        if input("Supprimer TOUS les produits ? Tape 'oui' pour confirmer : ").strip().lower() != "oui":
            print("Annulé.")
            return 1

    try:
        if args.only != "identity":
            print("== Nettoyage ==")
            keep = {p.categorie for p in ic.load_catalog(args.data_dir).parts}
            plan = plan_cleanup(client.list_json("products", "id,reference"),
                                client.list_json("categories", "id,id_parent,level_depth,name"),
                                client.list_json("manufacturers", "id,name"), keep, args.scope)
            run_cleanup(client, plan, args.apply)
        if args.only != "clean":
            print("\n== Identité ==")
            shop_path = Path(args.shop)
            base = json.loads(shop_path.read_text(encoding="utf-8")) if shop_path.exists() else {}
            settings = config.merge_identity(base)
            if args.apply:
                apply_identity(client, settings)
                upload_logos(client, settings.get("logo_text") or settings.get("shop_name", "SHOP"), args.logo)
            else:
                print("  Paramètres lus :", {k: v for k, v in settings.items() if v})
        print()
        show_tax_groups(client)
    except ic.PSError as exc:
        print(f"Erreur API PrestaShop :\n{exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Habille la boutique de démonstration :
  - images produits  : photos du dossier photos/ (<SKU>.jpg), sinon image de substitution générée ;
  - logo et favicon  : copiés dans le conteneur puis déclarés dans la configuration ;
  - accueil          : désactive les modules promotionnels de démo (bannière, slider, texte).

Usage :
  python shop_assets.py                 # tout
  python shop_assets.py --list-home     # liste les modules affichés sur l'accueil
  python shop_assets.py --only images   # ou logo, home
Configuration : .env (PS_API_KEY, SHOP_*, LOGO_PATH, HOME_DISABLE_MODULES).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import config
import import_catalog as ic
import setup_shop as ss

DEFAULT_HOME_MODULES = "ps_banner,ps_imageslider,ps_customtext"
IMG_EXT = (".jpg", ".jpeg", ".png")
WEB_ROOT = "/var/www/html"


# --------------------------------------------------------------------------- #
# Images produits
# --------------------------------------------------------------------------- #
def find_photos(photos_dir, sku):
    """Photos <SKU>.ext puis <SKU>_2.ext, <SKU>_3.ext... (ordre alphabétique)."""
    d = Path(photos_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.suffix.lower() in IMG_EXT and (p.stem == sku or p.stem.startswith(f"{sku}_")))


def _font(size):
    from PIL import ImageFont
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def make_placeholder(part, out_path, size=1000):
    """Image de substitution sobre (fond sombre, bandeau rouge) : jamais une vraie photo du produit."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (24, 24, 28))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, size, 110), fill=(200, 30, 30))
    d.text((40, 28), part.categorie.upper(), font=_font(52), fill=(255, 255, 255))
    f = _font(66)
    lines = []
    for raw in textwrap.wrap(part.nom, width=18) or [part.nom]:
        lines.append(raw)
    y = size // 2 - len(lines) * 45
    for line in lines:
        w = d.textlength(line, font=f)
        d.text(((size - w) / 2, y), line, font=f, fill=(245, 245, 245))
        y += 90
    d.text((40, size - 150), part.sku, font=_font(48), fill=(200, 200, 200))
    d.text((40, size - 80), "Image de démonstration", font=_font(34), fill=(130, 130, 130))
    img.save(out_path, "JPEG", quality=88)
    return Path(out_path)


def has_images(client, product_id):
    try:
        return len(client.get_xml(f"images/products/{product_id}").findall(".//declination")) > 0
    except ic.PSError:
        return False


def upload_product_images(client, catalog, photos_dir="photos", placeholder=True, log=print):
    stats = {"photos": 0, "substitution": 0, "déjà présentes": 0, "sans image": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for part in catalog.parts:
            pid = client.find_id("products", {"reference": part.sku})
            if pid is None:
                log(f"  ! {part.sku} introuvable dans la boutique (importe d'abord le catalogue)")
                continue
            if has_images(client, pid):
                stats["déjà présentes"] += 1
                continue
            files = find_photos(photos_dir, part.sku)
            kind = "photos"
            if not files and placeholder:
                files, kind = [make_placeholder(part, Path(tmp) / f"{part.sku}.jpg")], "substitution"
            if not files:
                stats["sans image"] += 1
                continue
            for f in files:
                client.upload_image(f"images/products/{pid}", f)
            stats[kind] += 1
    log("Images : " + ", ".join(f"{k} = {v}" for k, v in stats.items()))
    return stats


# --------------------------------------------------------------------------- #
# Logo et favicon
# --------------------------------------------------------------------------- #
def run(cmd, input_text=None):
    res = subprocess.run(cmd, input=input_text, text=True, capture_output=True)
    return res.returncode, (res.stdout + res.stderr).strip()


def compose_cp(runner, local, name):
    """Copie un fichier dans img/ du conteneur et corrige les droits (Apache tourne en www-data)."""
    dest = f"{WEB_ROOT}/img/{name}"
    steps = [["docker", "compose", "cp", str(local), f"prestashop:{dest}"],
             ["docker", "compose", "exec", "-T", "prestashop", "chown", "www-data:www-data", dest],
             ["docker", "compose", "exec", "-T", "prestashop", "chmod", "644", dest]]
    for cmd in steps:
        code, out = runner(cmd)
        if code != 0:
            raise RuntimeError(f"{' '.join(cmd)}\n{out}")


def install_logo(client, logo_path, favicon_path, runner=run, log=print):
    stamp = int(time.time())
    ext = Path(logo_path).suffix.lower() or ".png"
    logo_name, ico_name = f"logo-{stamp}{ext}", f"favicon-{stamp}.ico"
    compose_cp(runner, logo_path, logo_name)
    for conf in ("PS_LOGO", "PS_LOGO_MAIL", "PS_LOGO_INVOICE"):
        ss.set_configuration(client, conf, logo_name)
    if favicon_path:
        compose_cp(runner, favicon_path, ico_name)
        ss.set_configuration(client, "PS_FAVICON", ico_name)
    log(f"Logo installé : {logo_name}" + (f", favicon : {ico_name}" if favicon_path else ""))


# --------------------------------------------------------------------------- #
# Accueil
# --------------------------------------------------------------------------- #
def console(runner, *args):
    """Commande PrestaShop dans le conteneur, en www-data (sinon le cache devient illisible pour Apache)."""
    return runner(["docker", "compose", "exec", "-T", "-u", "www-data", "prestashop",
                   "php", "bin/console", *args])


def disable_home_modules(runner, modules, log=print):
    for m in modules:
        code, out = console(runner, "prestashop:module", "disable", m)
        log(f"  {m} : " + ("désactivé" if code == 0 else f"échec ({out.splitlines()[-1] if out else 'sans message'})"))
    code, out = console(runner, "cache:clear")
    log("Cache vidé." if code == 0 else f"! cache:clear a échoué : {out[-200:]}")
    log("Pour réactiver un module : docker compose exec -u www-data prestashop php bin/console "
        "prestashop:module enable <nom>")


def list_home_modules(runner, db_password, db_name, prefix="ps_", log=print):
    sql = (f"SELECT DISTINCT m.name, h.name AS hook FROM {prefix}hook_module hm "
           f"JOIN {prefix}hook h ON h.id_hook=hm.id_hook JOIN {prefix}module m ON m.id_module=hm.id_module "
           f"WHERE h.name IN ('displayHome','displayTop','displayTopColumn','displayWrapperTop') "
           f"AND m.active=1 ORDER BY h.name, m.name;")
    code, out = runner(["docker", "compose", "exec", "-T", "db", "mysql", "-uroot", f"-p{db_password}",
                        db_name, "-e", sql])
    log(out if code == 0 else f"Requête impossible :\n{out}")


# --------------------------------------------------------------------------- #
def identity():
    path = Path("shop.json")
    base = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return config.merge_identity(base)


def main(argv=None, runner=run):
    ap = argparse.ArgumentParser(description="Images, logo et accueil de la boutique de démonstration")
    ap.add_argument("--only", choices=["images", "logo", "home"])
    ap.add_argument("--photos-dir", default="photos")
    ap.add_argument("--no-placeholder", action="store_true", help="ne génère pas d'image de substitution")
    ap.add_argument("--logo", help="logo PNG (sinon LOGO_PATH, assets/logo.png, ou logo généré)")
    ap.add_argument("--modules", help="modules à désactiver sur l'accueil (défaut : HOME_DISABLE_MODULES)")
    ap.add_argument("--list-home", action="store_true", help="liste les modules de l'accueil puis quitte")
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args(argv)
    config.load_env()

    if args.list_home:
        list_home_modules(runner, config.get("DB_PASSWD"), config.get("DB_NAME"), config.get("DB_PREFIX"))
        return 0
    key = os.environ.get("PS_API_KEY")
    if not key:
        print("PS_API_KEY manquante : lance bootstrap.py ou renseigne le .env.")
        return 2
    client = ic.PrestaClient(config.shop_url(), key)
    try:
        if args.only in (None, "images"):
            print("== Images produits ==")
            cat = ic.load_catalog(args.data_dir)
            upload_product_images(client, cat, args.photos_dir, placeholder=not args.no_placeholder)
        if args.only in (None, "logo"):
            print("== Logo ==")
            ident = identity()
            text = ident.get("logo_text") or ident.get("shop_name") or "SHOP"
            custom = args.logo or os.environ.get("LOGO_PATH") or (
                "assets/logo.png" if Path("assets/logo.png").exists() else None)
            with tempfile.TemporaryDirectory() as tmp:
                gen_logo, gen_ico = ss.make_logo_files(text, tmp)
                logo = Path(custom) if custom else gen_logo
                if logo is None:
                    print("  Logo ignoré : installe Pillow ou fournis assets/logo.png")
                else:
                    install_logo(client, logo, gen_ico, runner)
        if args.only in (None, "home"):
            print("== Accueil ==")
            modules = [m.strip() for m in (args.modules or os.environ.get(
                "HOME_DISABLE_MODULES", DEFAULT_HOME_MODULES)).split(",") if m.strip()]
            disable_home_modules(runner, modules)
    except (ic.PSError, RuntimeError) as exc:
        print(f"Erreur :\n{exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

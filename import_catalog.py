#!/usr/bin/env python3
"""
JDM Garage : import d'un catalogue de pièces avec compatibilités dans PrestaShop.

Trois étapes :
  1. Chargement et validation des CSV (véhicules, moteurs, pièces).
  2. Rapport de cohérence en Markdown (matrice pièces x moteurs, couverture, alertes).
  3. Import dans PrestaShop via l'API webservice (catégories, caractéristiques, produits, stock).

Usage :
  python import_catalog.py                 # validation + rapport seulement (dry-run)
  python import_catalog.py --import        # validation + rapport + import dans la boutique

Configuration lue dans le .env.example : PS_API_KEY (et PS_URL, sinon http://localhost:PS_PORT).
"""
import argparse
import csv
import html
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import config

HOME_CATEGORY_ID = 2          # catégorie "Accueil" de PrestaShop
FEATURE_VEHICLE = "Véhicule"
FEATURE_ENGINE = "Moteur"
FEATURE_BRAND = "Marque"


# --------------------------------------------------------------------------- #
# 1. Modèle de données et validation
# --------------------------------------------------------------------------- #
@dataclass
class Vehicle:
    vehicle_id: str
    marque: str
    modele: str
    generation: str
    annees: str

    @property
    def label(self):
        return f"{self.marque} {self.modele} {self.generation}"


@dataclass
class Engine:
    engine_id: str
    vehicle_id: str
    code: str
    cylindree: str
    aspiration: str
    note: str


@dataclass
class Part:
    sku: str
    nom: str
    marque: str
    categorie: str
    prix_ht: float
    stock: int
    description: str
    compat_raw: str
    engines: set = field(default_factory=set)   # engine_id compatibles (après expansion)


@dataclass
class Catalog:
    vehicles: dict
    engines: dict
    parts: list
    errors: list
    warnings: list


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_catalog(data_dir):
    data_dir = Path(data_dir)
    errors, warnings = [], []

    vehicles = {}
    for r in read_csv(data_dir / "vehicles.csv"):
        vid = r["vehicle_id"].strip()
        if vid in vehicles:
            errors.append(f"vehicles.csv : vehicle_id en double '{vid}'")
            continue
        vehicles[vid] = Vehicle(vid, r["marque"].strip(), r["modele"].strip(),
                                r["generation"].strip(), r["annees"].strip())

    engines = {}
    for r in read_csv(data_dir / "engines.csv"):
        eid = r["engine_id"].strip()
        if eid in engines:
            errors.append(f"engines.csv : engine_id en double '{eid}'")
            continue
        if r["vehicle_id"].strip() not in vehicles:
            errors.append(f"engines.csv : moteur '{eid}' rattaché à un véhicule inconnu '{r['vehicle_id']}'")
        engines[eid] = Engine(eid, r["vehicle_id"].strip(), r["code_moteur"].strip(),
                              r["cylindree_cc"].strip(), r["aspiration"].strip(), r["note"].strip())

    parts, seen = [], set()
    for i, r in enumerate(read_csv(data_dir / "parts.csv"), start=2):
        sku = r["sku"].strip()
        where = f"parts.csv ligne {i} ({sku or 'sans SKU'})"
        if not sku:
            errors.append(f"{where} : SKU manquant")
            continue
        if sku in seen:
            errors.append(f"{where} : SKU en double")
            continue
        seen.add(sku)

        try:
            prix = float(r["prix_ht"].replace(",", "."))
            if prix <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{where} : prix invalide '{r['prix_ht']}'")
            prix = 0.0
        try:
            stock = int(r["stock"])
            if stock < 0:
                raise ValueError
        except ValueError:
            errors.append(f"{where} : stock invalide '{r['stock']}'")
            stock = 0

        for col in ("nom", "marque", "categorie", "description"):
            val = r[col].strip()
            if val[:1] in ("=", "+", "@") or "<script" in val.lower():
                errors.append(f"{where} : contenu suspect dans '{col}' (formule de tableur ou balise script)")

        part = Part(sku, r["nom"].strip(), r["marque"].strip(), r["categorie"].strip(),
                    prix, stock, r["description"].strip(), r["compat"].strip())
        part.engines = resolve_compat(part, vehicles, engines, errors, where)

        if not part.compat_raw:
            warnings.append(f"{sku} : aucune compatibilité renseignée (à vérifier avant mise en ligne)")
        if not part.description:
            warnings.append(f"{sku} : description vide")
        elif len(part.description) < 40:
            warnings.append(f"{sku} : description très courte ({len(part.description)} caractères)")
        if "ALL" in [t.strip() for t in part.compat_raw.split("|")] and len(part.compat_raw.split("|")) > 1:
            warnings.append(f"{sku} : 'ALL' combiné à d'autres valeurs (redondant)")
        parts.append(part)

    covered = set().union(*(p.engines for p in parts)) if parts else set()
    for eid in engines:
        if eid not in covered:
            warnings.append(f"Moteur {eid} : aucune pièce compatible dans le catalogue")

    return Catalog(vehicles, engines, parts, errors, warnings)


def resolve_compat(part, vehicles, engines, errors, where):
    """Transforme 'VEH:CEL|ENG:PRE-F20B|ALL' en ensemble d'engine_id."""
    result = set()
    if not part.compat_raw:
        return result
    for token in (t.strip() for t in part.compat_raw.split("|")):
        if token == "ALL":
            result |= set(engines)
        elif token.startswith("VEH:"):
            vid = token[4:]
            if vid not in vehicles:
                errors.append(f"{where} : véhicule inconnu '{vid}'")
            result |= {e.engine_id for e in engines.values() if e.vehicle_id == vid}
        elif token.startswith("ENG:"):
            eid = token[4:]
            if eid not in engines:
                errors.append(f"{where} : moteur inconnu '{eid}'")
            else:
                result.add(eid)
        else:
            errors.append(f"{where} : valeur de compatibilité invalide '{token}' (attendu ALL, VEH:x ou ENG:x)")
    return result


# --------------------------------------------------------------------------- #
# 2. Rapport de cohérence
# --------------------------------------------------------------------------- #
def write_report(cat, path):
    engine_ids = list(cat.engines)
    lines = ["# Rapport de compatibilité", ""]
    lines += [f"- Véhicules : {len(cat.vehicles)}",
              f"- Moteurs : {len(cat.engines)}",
              f"- Pièces : {len(cat.parts)}",
              f"- Erreurs bloquantes : {len(cat.errors)}",
              f"- Alertes : {len(cat.warnings)}", ""]

    if cat.errors:
        lines += ["## Erreurs (l'import est bloqué)", ""] + [f"- {e}" for e in cat.errors] + [""]
    if cat.warnings:
        lines += ["## Alertes", ""] + [f"- {w}" for w in cat.warnings] + [""]

    lines += ["## Couverture par moteur", "", "| Moteur | Véhicule | Pièces compatibles |", "|---|---|---|"]
    for eid, e in cat.engines.items():
        v = cat.vehicles.get(e.vehicle_id)
        n = sum(1 for p in cat.parts if eid in p.engines)
        note = f" ({e.note})" if e.note else ""
        lines.append(f"| {e.code}{note} | {v.label if v else '?'} | {n} |")

    lines += ["", "## Matrice pièces x moteurs", "",
              "| SKU | Pièce | " + " | ".join(cat.engines[e].code for e in engine_ids) + " |",
              "|---|---|" + "---|" * len(engine_ids)]
    for p in cat.parts:
        cells = ["✓" if e in p.engines else "" for e in engine_ids]
        lines.append(f"| {p.sku} | {p.nom} | " + " | ".join(cells) + " |")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 3. Client API PrestaShop
# --------------------------------------------------------------------------- #
class PSError(RuntimeError):
    pass


class PrestaClient:
    """Client minimal du webservice PrestaShop (authentification : clé API en nom d'utilisateur)."""

    def __init__(self, base_url, api_key, timeout=30):
        import requests
        self.base = base_url.rstrip("/") + "/api"
        self.session = requests.Session()
        self.session.auth = (api_key, "")
        self.timeout = timeout

    def _check(self, r):
        if r.status_code >= 400:
            raise PSError(f"HTTP {r.status_code} sur {r.request.method} {r.url}\n{r.text[:800]}")
        return r

    def language_ids(self):
        r = self._check(self.session.get(f"{self.base}/languages",
                                         params={"output_format": "JSON", "display": "[id]"},
                                         timeout=self.timeout))
        data = r.json().get("languages", [])
        return [int(x["id"]) for x in data]

    def find_id(self, resource, filters):
        params = {f"filter[{k}]": f"[{v}]" for k, v in filters.items()}
        params.update({"display": "[id]", "output_format": "JSON"})
        r = self._check(self.session.get(f"{self.base}/{resource}", params=params, timeout=self.timeout))
        data = r.json()
        items = data.get(resource, []) if isinstance(data, dict) else []
        return int(items[0]["id"]) if items else None

    def schema(self, resource):
        r = self._check(self.session.get(f"{self.base}/{resource}", params={"schema": "blank"},
                                         timeout=self.timeout))
        return ET.fromstring(r.content)

    def get_xml(self, path):
        r = self._check(self.session.get(f"{self.base}/{path}", timeout=self.timeout))
        return ET.fromstring(r.content)

    def create(self, resource, root):
        r = self._check(self.session.post(f"{self.base}/{resource}",
                                          data=ET.tostring(root, encoding="utf-8"),
                                          headers={"Content-Type": "text/xml"}, timeout=self.timeout))
        return int(ET.fromstring(r.content).find(".//id").text)

    def put(self, path, root):
        self._check(self.session.put(f"{self.base}/{path}",
                                     data=ET.tostring(root, encoding="utf-8"),
                                     headers={"Content-Type": "text/xml"}, timeout=self.timeout))

    def list_json(self, resource, fields):
        """Liste des ressources avec les champs demandés, en JSON."""
        r = self._check(self.session.get(f"{self.base}/{resource}",
                                         params={"display": f"[{fields}]", "output_format": "JSON"},
                                         timeout=self.timeout))
        data = r.json()
        return data.get(resource, []) if isinstance(data, dict) else []

    def delete(self, path):
        self._check(self.session.delete(f"{self.base}/{path}", timeout=self.timeout))

    def upload_image(self, path, file_path):
        import mimetypes
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            self._check(self.session.post(f"{self.base}/{path}",
                                          files={"image": (Path(file_path).name, f, mime)},
                                          timeout=self.timeout))


# --------------------------------------------------------------------------- #
# Aides XML
# --------------------------------------------------------------------------- #
def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def set_text(parent, tag, value):
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    for child in list(node):
        node.remove(child)
    node.text = str(value)


def set_ml(parent, tag, text, lang_ids):
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    for child in list(node):
        node.remove(child)
    node.text = None
    for lid in lang_ids:
        ET.SubElement(node, "language", {"id": str(lid)}).text = text


def remove_tags(parent, tags):
    for t in tags:
        node = parent.find(t)
        if node is not None:
            parent.remove(node)


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
PRODUCT_READONLY = ["manufacturer_name", "quantity", "position_in_category", "type",
                    "id_default_image", "id_default_combination", "date_add", "date_upd"]
CATEGORY_READONLY = ["level_depth", "nb_products_recursive", "date_add", "date_upd"]


class Importer:
    def __init__(self, client, catalog, tax_rules_group=None, log=print):
        self.c = client
        self.cat = catalog
        self.tax = tax_rules_group
        self.log = log
        self.langs = []
        self.category_ids = {}
        self.feature_ids = {}
        self.value_ids = {}
        self.stats = {"créés": 0, "ignorés (déjà présents)": 0}

    # -- caractéristiques et catégories ------------------------------------- #
    def ensure_category(self, name):
        if name in self.category_ids:
            return self.category_ids[name]
        cid = self.c.find_id("categories", {"name": name})
        if cid is None:
            root = self.c.schema("categories")
            cat = root.find("category")
            remove_tags(cat, CATEGORY_READONLY)
            assoc = cat.find("associations")
            if assoc is not None:
                cat.remove(assoc)
            set_text(cat, "id_parent", HOME_CATEGORY_ID)
            set_text(cat, "active", 1)
            set_ml(cat, "name", name, self.langs)
            set_ml(cat, "link_rewrite", slugify(name), self.langs)
            cid = self.c.create("categories", root)
            self.log(f"  catégorie créée : {name} (id {cid})")
        self.category_ids[name] = cid
        return cid

    def ensure_feature(self, name):
        if name in self.feature_ids:
            return self.feature_ids[name]
        fid = self.c.find_id("product_features", {"name": name})
        if fid is None:
            root = self.c.schema("product_features")
            feat = root.find("product_feature")
            set_ml(feat, "name", name, self.langs)
            fid = self.c.create("product_features", root)
            self.log(f"  caractéristique créée : {name} (id {fid})")
        self.feature_ids[name] = fid
        return fid

    def ensure_value(self, feature_name, value):
        key = (feature_name, value)
        if key in self.value_ids:
            return self.value_ids[key]
        fid = self.ensure_feature(feature_name)
        vid = self.c.find_id("product_feature_values", {"id_feature": fid, "value": value})
        if vid is None:
            root = self.c.schema("product_feature_values")
            val = root.find("product_feature_value")
            set_text(val, "id_feature", fid)
            set_text(val, "custom", 0)
            set_ml(val, "value", value, self.langs)
            vid = self.c.create("product_feature_values", root)
        self.value_ids[key] = vid
        return vid

    # -- produits ------------------------------------------------------------ #
    def feature_pairs(self, part):
        pairs = {(FEATURE_BRAND, part.marque)}
        for eid in sorted(part.engines):
            e = self.cat.engines[eid]
            v = self.cat.vehicles[e.vehicle_id]
            pairs.add((FEATURE_VEHICLE, v.label))
            pairs.add((FEATURE_ENGINE, e.code))
        return sorted(pairs)

    def create_product(self, part):
        cat_id = self.ensure_category(part.categorie)
        root = self.c.schema("products")
        prod = root.find("product")
        remove_tags(prod, PRODUCT_READONLY)

        set_text(prod, "reference", part.sku)
        set_text(prod, "price", f"{part.prix_ht:.6f}")
        set_text(prod, "id_category_default", cat_id)
        set_text(prod, "id_shop_default", 1)
        if self.tax is not None:
            set_text(prod, "id_tax_rules_group", self.tax)
        for tag in ("active", "state", "available_for_order", "show_price", "minimal_quantity"):
            set_text(prod, tag, 1)
        set_ml(prod, "name", part.nom, self.langs)
        set_ml(prod, "link_rewrite", slugify(part.nom), self.langs)
        set_ml(prod, "description_short", f"<p>{html.escape(part.description)}</p>", self.langs)
        set_ml(prod, "description", f"<p>{html.escape(part.description)}</p>", self.langs)
        # description_short : PrestaShop attend du HTML, on échappe aussi

        assoc = prod.find("associations")
        if assoc is None:
            assoc = ET.SubElement(prod, "associations")
        for child in list(assoc):
            assoc.remove(child)
        cats = ET.SubElement(assoc, "categories")
        for cid in (HOME_CATEGORY_ID, cat_id):
            ET.SubElement(ET.SubElement(cats, "category"), "id").text = str(cid)
        feats = ET.SubElement(assoc, "product_features")
        for fname, value in self.feature_pairs(part):
            pf = ET.SubElement(feats, "product_feature")
            ET.SubElement(pf, "id").text = str(self.ensure_feature(fname))
            ET.SubElement(pf, "id_feature_value").text = str(self.ensure_value(fname, value))

        pid = self.c.create("products", root)
        self.set_stock(pid, part.stock)
        return pid

    def set_stock(self, product_id, quantity):
        sid = self.c.find_id("stock_availables", {"id_product": product_id, "id_product_attribute": 0})
        if sid is None:
            self.log(f"  ! stock introuvable pour le produit {product_id}")
            return
        root = self.c.get_xml(f"stock_availables/{sid}")
        sa = root.find("stock_available")
        set_text(sa, "quantity", quantity)
        set_text(sa, "depends_on_stock", 0)
        self.c.put(f"stock_availables/{sid}", root)

    def run(self):
        self.langs = self.c.language_ids()
        if not self.langs:
            raise PSError("Aucune langue trouvée : vérifie les droits de la clé API sur 'languages'.")
        for part in self.cat.parts:
            if self.c.find_id("products", {"reference": part.sku}) is not None:
                self.stats["ignorés (déjà présents)"] += 1
                self.log(f"= {part.sku} déjà présent, ignoré")
                continue
            pid = self.create_product(part)
            self.stats["créés"] += 1
            self.log(f"+ {part.sku} créé (id {pid}), {len(part.engines)} moteurs compatibles")
        return self.stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Import de catalogue JDM dans PrestaShop")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--report", default="reports/rapport_compatibilite.md")
    ap.add_argument("--import", dest="do_import", action="store_true",
                    help="pousse le catalogue dans PrestaShop (sinon : validation et rapport seulement)")
    ap.add_argument("--tax-rules-group", type=int, default=None,
                    help="id du groupe de règles de taxe (TVA) à appliquer aux produits")
    args = ap.parse_args(argv)
    config.load_env()

    cat = load_catalog(args.data_dir)
    write_report(cat, args.report)
    print(f"{len(cat.parts)} pièces, {len(cat.engines)} moteurs, {len(cat.vehicles)} véhicules")
    print(f"{len(cat.errors)} erreur(s), {len(cat.warnings)} alerte(s) -> rapport : {args.report}")
    for e in cat.errors:
        print(f"  ERREUR : {e}")
    for w in cat.warnings:
        print(f"  alerte : {w}")
    if cat.errors:
        print("Import impossible tant que les erreurs ne sont pas corrigées.")
        return 1
    if not args.do_import:
        print("Dry-run terminé. Relance avec --import pour envoyer le catalogue à PrestaShop.")
        return 0

    url, key = config.shop_url(), os.environ.get("PS_API_KEY")
    if not key:
        print("PS_API_KEY manquante : renseigne-la dans le .env.example (ou lance bootstrap.py, qui la crée).")
        return 2
    try:
        stats = Importer(PrestaClient(url, key), cat, tax_rules_group=args.tax_rules_group).run()
    except PSError as exc:
        print(f"Erreur API PrestaShop :\n{exc}")
        return 3
    print("Import terminé :", ", ".join(f"{k} = {v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

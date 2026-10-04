# JDM Garage : import de catalogue avec compatibilités véhicules (PrestaShop)

Démonstrateur : un script Python lit trois fichiers CSV (véhicules, moteurs, pièces), contrôle leur
cohérence, génère un rapport de compatibilité, puis crée automatiquement dans PrestaShop les catégories,
les caractéristiques, les produits et le stock. Le client filtre ensuite le catalogue par véhicule et par
moteur grâce au module de recherche à facettes natif.

> Toutes les données (pièces, marques, prix, compatibilités) sont **fictives** et servent uniquement à la démonstration.
> Elles ne doivent pas être utilisées pour choisir de vraies pièces.

## Données de démo
- 3 véhicules : Toyota Celica T200, Honda Prelude BB, Nissan Silvia S14
- 6 moteurs (2 par véhicule), dont le F20B en swap sur Prelude
- 30 pièces réparties en 7 catégories

Format de la colonne `compat` de `parts.csv` : `ALL` (universel), `VEH:CEL` (tous les moteurs du véhicule),
`ENG:CEL-3SGTE` (un moteur précis). Les valeurs se combinent avec `|`.

## Configuration : le fichier .env
```bash
cp .env.example .env      # puis édite : mots de passe, port, identité de la boutique
```
Le `.env` est lu par Docker Compose **et** par les scripts Python. Il est dans `.gitignore` : ne le publie jamais.
Les variables déjà définies dans ton terminal l'emportent sur le `.env`. Si tu changes `DB_PASSWD`, `ADMIN_*` ou
`PS_PORT` après une première installation, relance avec `python bootstrap.py --reset`.

## Tout en une commande
```bash
pip install -r requirements.txt
python bootstrap.py --reset
```
Le script lance Docker, attend la fin de l'installation, crée la clé API directement en base (et l'écrit dans
`PS_API_KEY` du `.env`), nettoie la démo, applique l'identité (variables `SHOP_*` du `.env`, sinon `shop.json`),
détecte la TVA à 20 %, importe `data/*.csv` et retire le droit DELETE de la clé. Il est relançable sans doublons (`--skip-install` si la boutique tourne déjà).
Les étapes manuelles ci-dessous restent utiles en cas de blocage.

## Images, logo et accueil
```bash
python shop_assets.py --list-home   # voir les modules actifs sur l'accueil
python shop_assets.py               # images + logo + accueil
python shop_assets.py --only images # ou logo, home
```
- **Images :** dépose tes photos dans `photos/` (`FRE-001.jpg`, `FRE-001_2.jpg`...). Sans photo, une image de
  substitution est générée : à remplacer avant toute mise en ligne. Les produits qui ont déjà une image sont ignorés.
- **Logo :** `assets/logo.png` (sinon logo texte généré). Le fichier est copié dans le conteneur Docker et déclaré dans la configuration.
- **Accueil :** les modules promotionnels de démo sont désactivés (réversible avec `prestashop:module enable <nom>`).
  Les commandes PrestaShop tournent en `www-data`, sinon le cache devient illisible pour Apache.

## Installation (manuelle)
1. Lancer la boutique : `docker compose up -d` (patienter quelques minutes), puis ouvrir http://localhost:8080
   (back-office : http://localhost:8080/admin-dev, identifiants de démo dans `docker-compose.yml`).
2. Activer l'API : *Paramètres avancés > Webservice*, activer le webservice, puis ajouter une clé avec
   les droits GET/POST/PUT/DELETE sur : `categories`, `languages`, `product_features`, `product_feature_values`,
   `products`, `stock_availables`, `manufacturers`, `configurations`, `tax_rule_groups`, `images`.
3. Installer les dépendances (Pillow sert à générer un logo texte) : `pip install -r requirements.txt`

## Préparer la boutique (nettoyage de la démo et identité)
```bash
# identité : variables SHOP_* du .env (sinon shop.json)
python setup_shop.py            # simulation : n'écrit rien
python setup_shop.py --apply    # exécute
```
Par défaut, seuls les produits de démo (`demo_*`) sont supprimés, ainsi que les catégories et marques de démo.
Les catégories « Racine » et « Accueil » sont protégées. Le logo et le favicon sont envoyés en best-effort :
si l'API les refuse, le script l'indique et il faut les régler dans *Design > Thème & logo*.

## Utilisation
```bash
python import_catalog.py            # validation + rapport seulement
# PS_API_KEY est lue dans le .env
python import_catalog.py --import   # envoi dans PrestaShop
```
Relancer l'import est sans risque : les références déjà présentes sont ignorées.
Le rapport est écrit dans `reports/rapport_compatibilite.md`.

## Filtre « Mon garage »
Dans *Paramètres de la boutique > Recherche à facettes* (module Faceted search), créer un modèle de
filtres avec les caractéristiques **Véhicule**, **Moteur** et **Marque**, appliqué aux catégories.
Les pièces universelles apparaissent pour tous les véhicules.

## Limites et suite
- Pas de sélecteur de véhicule dédié : le filtre repose sur les facettes natives.
- L'import ne met pas à jour les produits existants.
- Une pièce sans compatibilité (alerte du rapport) est importée mais devrait être vérifiée avant publication.
- Pistes : mise à jour des produits, import des images, module d'affichage « compatible / à vérifier ».

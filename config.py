"""Configuration par fichier .env, partagée par docker compose et les scripts Python.

Docker Compose lit automatiquement le .env du dossier (substitution ${VAR:-défaut}).
Les scripts le chargent avec load_env() sans écraser les variables déjà définies dans l'environnement.
"""
import os
from pathlib import Path

ENV_PATH = Path(".env")

DEFAULTS = {
    "PS_PORT": "8080",
    "PS_FOLDER_ADMIN": "admin-dev",
    "DB_NAME": "prestashop",
    "DB_PASSWD": "admin",
    "DB_PREFIX": "ps_",
    "ADMIN_MAIL": "demo@example.com",
    "ADMIN_PASSWD": "ChangeMe_12345",
}

# variable d'environnement -> clé de shop.json
IDENTITY_ENV = {
    "SHOP_NAME": "shop_name", "SHOP_EMAIL": "email", "SHOP_PHONE": "phone", "SHOP_ADDRESS": "address",
    "SHOP_POSTCODE": "postcode", "SHOP_CITY": "city", "SHOP_LOGO_TEXT": "logo_text",
}


def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def load_env(path=None, environ=None):
    """Charge le .env dans l'environnement (les variables déjà définies gardent la priorité)."""
    path = Path(path) if path else ENV_PATH
    environ = os.environ if environ is None else environ
    if not path.exists():
        return {}
    values = parse_env(path.read_text(encoding="utf-8"))
    for k, v in values.items():
        environ.setdefault(k, v)
    return values


def update_env(key, value, path=None):
    """Écrit ou met à jour une variable dans le .env (le crée si besoin)."""
    path = Path(path) if path else ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().split("=", 1)[0].replace("export ", "").strip() == key and "=" in line:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def get(name, environ=None):
    environ = os.environ if environ is None else environ
    return environ.get(name) or DEFAULTS.get(name, "")


def shop_url(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("PS_URL") or f"http://localhost:{get('PS_PORT', environ)}"


def admin_url(environ=None):
    return f"{shop_url(environ)}/{get('PS_FOLDER_ADMIN', environ)}"


def merge_identity(base, environ=None):
    """Les variables SHOP_* du .env l'emportent sur shop.json."""
    environ = os.environ if environ is None else environ
    merged = dict(base)
    for env_name, key in IDENTITY_ENV.items():
        if environ.get(env_name, "").strip():
            merged[key] = environ[env_name].strip()
    return merged

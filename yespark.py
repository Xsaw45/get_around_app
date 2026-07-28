"""
yespark.py — Densité de parkings YesPark à proximité (feature ML)
===================================================================

YesPark loue des places de parking au mois (yespark.fr, ~7500 emplacements en
France). Pas d'open data officiel avec une couverture complète sur notre zone
(le jeu ouvert data.issy.com n'a que 8 parkings, tout dans le grand ouest
parisien) : on lit donc leur sitemap public (`sitemap.xml`, ressource
explicitement publiée pour les crawlers — `robots.txt` autorise le user-agent
générique sur `/parkings/*`), qui donne déjà l'adresse de chaque parking en
métadonnée d'image. On géocode ensuite ces adresses via l'**API officielle
Adresse** (api-adresse.data.gouv.fr, gratuite, gouvernementale — même famille
que le principe GBFS du reste du projet) plutôt que d'aller chercher les
coordonnées sur chacune des ~3800 pages individuelles : même résultat (position
de chaque parking), une seule requête sur yespark.fr au lieu de milliers.

Sortie : data/yespark_parkings.csv (id, adresse, code_postal, lat, lon, score_geocodage)
— snapshot statique (les parkings changent peu), à régénérer manuellement de
temps en temps via `python yespark.py`, PAS par le collecteur GBFS (ingest.py).
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from config import DATA_DIR, USER_AGENT, HTTP_TIMEOUT, HTTP_RETRIES, HTTP_BACKOFF

SITEMAP_URL = "https://www.yespark.fr/sitemap/parkings.xml"
GEOCODE_URL = "https://api-adresse.data.gouv.fr/search/"
OUT_PATH = DATA_DIR / "yespark_parkings.csv"

# Zone couverte par notre collecte GBFS (cf. config.SYSTEMS / IDF_BBOX) :
# Paris + petite couronne (départements 75/92/93/94) + est grande couronne
# (Meaux, Chessy, Serris, Torcy). Filtre appliqué AVANT géocodage pour ne
# solliciter ni yespark.fr ni l'API Adresse au-delà de ce qui nous sert.
IDF_POSTAL_PREFIXES = ("75", "92", "93", "94")
GRANDE_COURONNE_EST = {"77100", "77200", "77700"}

_SM_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
         "im": "http://www.google.com/schemas/sitemap-image/1.1"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:                      # réseau/HTTP transitoire
            last_exc = exc
            time.sleep(HTTP_BACKOFF * (attempt + 1))
    raise last_exc


def _in_scope(postal_code: str) -> bool:
    return postal_code.startswith(IDF_POSTAL_PREFIXES) or postal_code in GRANDE_COURONNE_EST


def fetch_candidates() -> list[dict]:
    """Une seule requête sur le sitemap public -> parkings dans notre zone,
    avec leur adresse texte (pas encore géocodée)."""
    raw = _get(SITEMAP_URL)
    root = ET.fromstring(raw)
    out = []
    for u in root.findall("sm:url", _SM_NS):
        loc = u.find("sm:loc", _SM_NS)
        img = u.find("im:image", _SM_NS)
        if loc is None or img is None:
            continue
        addr_el = img.find("im:geo_location", _SM_NS)
        addr = addr_el.text if addr_el is not None else None
        if not addr:
            continue
        m = re.search(r"\b(\d{5})\b", addr)
        if not m or not _in_scope(m.group(1)):
            continue
        pid = loc.text.rstrip("/").rsplit("/", 1)[-1].split("-", 1)[0]
        out.append({"id": pid, "url": loc.text, "address": addr, "postal_code": m.group(1)})
    return out


def geocode(address: str) -> tuple[float, float, float] | None:
    """Géocode une adresse via l'API officielle Adresse (data.gouv.fr)."""
    url = f"{GEOCODE_URL}?q={urllib.parse.quote(address)}&limit=1"
    try:
        raw = _get(url)
    except Exception:
        return None
    feats = json.loads(raw).get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    score = feats[0]["properties"].get("score", 0.0)
    return lat, lon, score


def fetch_all(candidates: list[dict], pause: float = 0.1) -> list[dict]:
    rows = []
    for i, c in enumerate(candidates):
        geo = geocode(c["address"])
        if geo:
            lat, lon, score = geo
            rows.append({**c, "lat": lat, "lon": lon, "geocode_score": score})
        if pause:
            time.sleep(pause)
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(candidates)} géocodés...")
    return rows


def save(rows: list[dict], path=None) -> None:
    path = path or OUT_PATH
    fields = ["id", "url", "address", "postal_code", "lat", "lon", "geocode_score"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} parkings YesPark écrits dans {path}")


if __name__ == "__main__":
    print("Lecture du sitemap YesPark...")
    candidates = fetch_candidates()
    print(f"{len(candidates)} parkings dans la zone de collecte -> géocodage "
         f"(API officielle Adresse, ~{len(candidates) * 0.15 / 60:.0f} min)...")
    rows = fetch_all(candidates)
    save(rows)

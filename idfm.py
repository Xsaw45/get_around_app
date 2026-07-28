"""
idfm.py — Densité de transport en commun lourd à proximité (feature ML)
==========================================================================

Accessibilité en transport en commun (métro/RER/tram/train) — probablement
l'un des signaux les plus forts de demande de mobilité partagée, souvent plus
déterminant que le prix lui-même. Source : Île-de-France Mobilités, open data
**officiel** (data.iledefrance-mobilites.fr, dataset "arrets-lignes"), export
CSV en masse en UNE SEULE requête (pas de scraping, pas de géocodage
nécessaire — lat/lon déjà fournis par le dataset).

Le dataset brut est 1 ligne par (arrêt, ligne desservant cet arrêt) — dominé
par le bus (53k/55k lignes, quasi omniprésent en zone dense, peu discriminant
à l'échelle de Paris). On exclut le bus et on déduplique par stop_id pour ne
garder que les arrêts de transport LOURD (métro, RER, tram, train) : ~1900
arrêts, un signal beaucoup plus différenciant d'un quartier à l'autre.

Sortie : data/idfm_arrets.csv (stop_id, nom, commune, mode, lat, lon)
— snapshot statique (le réseau change peu), à régénérer manuellement de temps
en temps via `python idfm.py`, PAS par le collecteur GBFS (ingest.py).
"""
from __future__ import annotations

import csv
import urllib.request

from config import DATA_DIR, USER_AGENT, HTTP_TIMEOUT

EXPORT_URL = ("https://data.iledefrance-mobilites.fr/api/v2/catalog/datasets/"
             "arrets-lignes/exports/csv?limit=-1")
OUT_PATH = DATA_DIR / "idfm_arrets.csv"

# transport LOURD seulement : le bus est quasi omniprésent en zone dense
# (53k/55k lignes du dataset), donc peu discriminant comme signal de quartier.
HEAVY_MODES = {"Metro", "Tramway", "LocalTrain", "RapidTransit", "regionalRail",
              "RailShuttle", "CableWay", "Funicular"}


def fetch() -> list[dict]:
    """Une seule requête (export CSV en masse) -> arrêts de transport lourd,
    dédupliqués par stop_id (le dataset a 1 ligne par arrêt × ligne)."""
    req = urllib.request.Request(EXPORT_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
    reader = csv.DictReader(raw.splitlines(), delimiter=";")
    seen: set[str] = set()
    rows = []
    for r in reader:
        if r.get("mode") not in HEAVY_MODES:
            continue
        sid = r["stop_id"]
        if sid in seen:
            continue
        seen.add(sid)
        rows.append({"stop_id": sid, "nom": r["stop_name"], "commune": r["nom_commune"],
                    "mode": r["mode"], "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])})
    return rows


def save(rows: list[dict], path=None) -> None:
    path = path or OUT_PATH
    fields = ["stop_id", "nom", "commune", "mode", "lat", "lon"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} arrêts de transport lourd écrits dans {path}")


if __name__ == "__main__":
    print("Téléchargement du dataset IDFM (export CSV officiel en masse)...")
    rows = fetch()
    save(rows)

"""
config.py — Paramètres du collecteur GBFS Getaround
===================================================

Source de données : le flux ouvert GBFS v3 que Getaround publie par obligation
légale (LOM) sur data.gouv.fr. On ne scrape RIEN : on lit un JSON officiel.

Tout ce qui peut changer (périmètre géo, cadence, systèmes) est ici, pas dans le
code de collecte.
"""
from __future__ import annotations
from pathlib import Path

# --------------------------------------------------------------------------
# Source GBFS
# --------------------------------------------------------------------------
GBFS_MANIFEST = "https://fr.getaround.com/gbfs/manifest?country_code=FR"
GBFS_SYSTEM_TMPL = "https://fr.getaround.com/gbfs/v3/{system}/gbfs"

# Systèmes à collecter. `getaround_paris` couvre Paris + petite couronne
# (69 communes, plafonné à 1000 véhicules par le flux). Pour étendre à la
# grande couronne (Meaux, Val d'Europe, 77…), ajoute les slugs de ces villes :
#   SYSTEMS = ["paris", "meaux", "torcy", "chessy"]
# Les doublons entre systèmes sont dédupliqués par listing_id à l'ingestion.
SYSTEMS = ["paris"]

# --------------------------------------------------------------------------
# Filtre géographique — bounding box Île-de-France (sécurité anti hors-zone)
# Un véhicule hors de cette box est ignoré (utile si on ajoute des systèmes
# qui débordent). Départements 75/77/78/91/92/93/94/95.
# --------------------------------------------------------------------------
IDF_BBOX = dict(lat_min=48.10, lat_max=49.25, lon_min=1.40, lon_max=3.60)

# --------------------------------------------------------------------------
# Stockage
# --------------------------------------------------------------------------
# data/ = stockage CANONIQUE, committé dans git (CSV partitionné par jour).
# Portable, léger, survit aux runners jetables de GitHub Actions.
DATA_DIR = Path(__file__).parent / "data"
# SQLite = miroir LOCAL optionnel (pratique pour du SQL ad hoc, non committé).
DB_PATH = Path(__file__).parent / "getaround_gbfs.sqlite"
EXPORT_DIR = Path(__file__).parent / "exports"       # datasets ML (parquet/csv)
REPORTS_DIR = Path(__file__).parent / "reports"      # rapport visuel (committé)

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
USER_AGENT = "getaround-idf-study/1.0 (open-data GBFS; personal market research)"
HTTP_TIMEOUT = 30          # secondes
HTTP_RETRIES = 3
HTTP_BACKOFF = 5.0         # secondes, doublé à chaque retry

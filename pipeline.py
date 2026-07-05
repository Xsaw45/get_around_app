"""
pipeline.py — Features & scoring rentabilité (base de l'analyse marché + ML)
===========================================================================

Transforme les snapshots en une TABLE DE FEATURES par véhicule, puis en
classements par segment / modèle. C'est la couche qui répond à « quelles voitures
sont les plus rentables et efficaces ».

Trois indicateurs, du plus brut au plus décisionnel :
  - taux_occupation      : demande pure (part du temps où la voiture est louée)
  - revenu_jour / an     : occupation × prix (efficacité de génération de CA)
  - roi_annuel           : revenu annuel / coût d'acquisition (rentabilité du capital)

⚠️ Tant que peu de passages sont accumulés, l'occupation (donc revenu/ROI) est
BRUITÉE. Le code est correct dès maintenant ; les CONCLUSIONS se fiabilisent avec
les jours de collecte. `min_passages` filtre les véhicules trop peu observés.

Le coût d'acquisition n'est PAS dans le GBFS : on l'estime par segment
(ACQUISITION_DEFAUT, à ajuster avec tes vraies valeurs Argus).
"""
from __future__ import annotations
import pandas as pd
import numpy as np

from features import (load_snapshots, collection_grid,
                      utilization_per_vehicle, rental_episodes)


# ---------------------------------------------------------------------------
# 1. SEGMENTATION  (make/model -> segment marché)
# ---------------------------------------------------------------------------
# Règles par mots-clés, dans l'ordre de priorité. On teste le modèle en
# minuscules ; la 1re règle qui matche gagne.
_SEG_RULES: list[tuple[str, tuple[str, ...]]] = [
    # vans "passagers" (9 places) AVANT les fourgons cargo
    ("Minibus", ("passenger", "combi", "kombi", "passager", "tourer",
                 "spacetourer", "space tourer", "traveller", "verso",
                 "caravelle", "vivaro life", "9 places", "8 places")),
    # fourgons / utilitaires cargo
    ("Utilitaire", ("trafic", "master", "jumpy", "jumper", "boxer", "ducato",
                    "expert", "berlingo", "kangoo", "partner", "fiorino",
                    "doblo", "combo", "nv200", "nv300", "nv400", "talento",
                    "vivaro", "movano", "daily", "crafter", "sprinter", "vito",
                    "transporter", "transit", "proace", "caddy", "fourgon",
                    "utilitaire", "scudo", "dokker")),
    ("Cabriolet", ("cabrio", "cabriolet", "roadster", "eos", "mx-5", "z4",
                   "boxster", "miata")),
    # SUV / crossovers (avant citadines : "3008" ne doit pas tomber en "308")
    ("SUV", ("2008", "3008", "5008", "captur", "kadjar", "duster", "qashqai",
             "tiguan", "t-roc", "t-cross", "juke", "aircross", "arkana",
             "kona", "tucson", "puma", "mokka", "rav4", "koleos", "x1", "x3",
             "q3", "q5", "gla", "glc", "yaris cross", "c-hr", "arona",
             "crossland", "crossback", "3008")),
    # monospaces / familiales
    ("Familiale", ("scenic", "scénic", "espace", "touran", "picasso",
                   "spacetourer", "5008", "zafira", "sharan", "galaxy",
                   "lodgy", "jogger", "meriva")),
    # berlines / compactes
    ("Berline", ("megane", "mégane", "308", "508", "c4", "golf", "astra",
                 "focus", "civic", "serie 3", "série 3", "serie 1", "série 1",
                 "classe", "a3", "a4", "corolla", "leon", "león", "octavia",
                 "insignia", "passat", "mondeo", "tipo", "logan", "308")),
    # citadines
    ("Citadine", ("208", "108", "clio", "c3", "twingo", "polo", "500", "aygo",
                  "corsa", "fiesta", "micra", "i10", "i20", " up", "c1",
                  "panda", " ka", "yaris", "swift", "ibiza", "a1", "adam",
                  "spring", "zoe", "mini", "e-208", "e-up", "fabia", "sandero",
                  "ds 3", "ds3", "fortwo", "citigo", "celerio", "ypsilon",
                  "punto", "note", "modus", "up!")),
]


def segment_of(make, model) -> str:
    m = f"{make or ''} {model or ''}".lower()
    for seg, keys in _SEG_RULES:
        if any(k in m for k in keys):
            return seg
    return "Autre"


# coût d'acquisition médian estimé par segment (€) — À AJUSTER (Argus / ton marché)
ACQUISITION_DEFAUT = {
    "Utilitaire": 15000, "Citadine": 8000, "Berline": 12000, "SUV": 16000,
    "Minibus": 22000, "Familiale": 14000, "Cabriolet": 16000, "Autre": 12000,
}


# ---------------------------------------------------------------------------
# 2. FEATURES PAR VÉHICULE
# ---------------------------------------------------------------------------
def build_features(snap: pd.DataFrame | None = None,
                   acquisition_cost: dict | None = None,
                   min_passages: int = 3) -> pd.DataFrame:
    """Une ligne par véhicule : identité, segment, prix, occupation, revenu, ROI.

    min_passages : ignore les véhicules vus à moins de N passages (bruit)."""
    if snap is None:
        snap = load_snapshots()
    grid = collection_grid()
    acq = acquisition_cost or ACQUISITION_DEFAUT

    util = utilization_per_vehicle(snap, grid=grid)         # occupation + épisodes
    util = util[util["n_passages"] >= min_passages].copy()

    # segment + département (75/92/93/94/77…) via longitude approx
    util["segment"] = [segment_of(mk, md)
                       for mk, md in zip(util["make"], util["model"])]
    # revenu réalisé estimé
    util["revenu_jour"] = util["taux_occupation"] * util["daily_rate"]
    util["revenu_annuel"] = util["revenu_jour"] * 365.0
    util["cout_acquisition"] = util["segment"].map(acq)
    util["roi_annuel"] = util["revenu_annuel"] / util["cout_acquisition"]
    return util


# ---------------------------------------------------------------------------
# 3. CLASSEMENTS  (agrégation par segment / modèle)
# ---------------------------------------------------------------------------
def rank_by(features: pd.DataFrame, by: str = "segment",
            min_n: int = 5) -> pd.DataFrame:
    """Agrège les features par `segment`, `make`, `model`… et classe par ROI."""
    key = by
    if by == "model":
        features = features.assign(model=features["make"].fillna("?") + " "
                                   + features["model"].fillna("?"))
    agg = (features.groupby(key)
           .agg(n_vehicules=("uid", "nunique"),
                prix_jour_moy=("daily_rate", "mean"),
                occupation_moy=("taux_occupation", "mean"),
                revenu_annuel_moy=("revenu_annuel", "mean"),
                cout_acq=("cout_acquisition", "median"),
                roi_annuel=("roi_annuel", "mean"))
           .reset_index())
    agg = agg[agg["n_vehicules"] >= min_n]
    return agg.sort_values("roi_annuel", ascending=False).round(3)


# ---------------------------------------------------------------------------
# APERÇU
# ---------------------------------------------------------------------------
def _preview():
    snap = load_snapshots()
    n_ts = snap["snapshot_ts"].nunique()
    feats = build_features(snap)
    print(f"{len(feats)} véhicules ({n_ts} passages). "
          f"⚠️ occupation/ROI préliminaires tant que n_ts est faible.\n")
    print("=== Couverture des segments (classification) ===")
    print(feats["segment"].value_counts().to_string())
    print("\n=== Classement RENTABILITÉ par segment (préliminaire) ===")
    print(rank_by(feats, by="segment").to_string(index=False))
    print("\n=== Top modèles par ROI (préliminaire, >=5 véh.) ===")
    print(rank_by(feats, by="model", min_n=5).head(12).to_string(index=False))


if __name__ == "__main__":
    _preview()

"""
pipeline.py — Features & scoring rentabilité (base de l'analyse marché + ML)
===========================================================================

Transforme les snapshots en une TABLE DE FEATURES par véhicule, puis en
classements par segment / modèle. C'est la couche qui répond à « quelles voitures
sont les plus rentables et efficaces ».

Indicateurs, du plus brut au plus décisionnel :
  - taux_occupation      : demande pure (part du temps où la voiture est louée)
  - revenu_jour / an     : occupation × prix (efficacité de génération de CA)
  - roi_annuel           : revenu annuel / coût d'acquisition (rentabilité brute)
  - roi_net              : après entretien (via reliability.py) — rentabilité nette

Le coût d'acquisition n'est PAS dans le GBFS : on l'ESTIME par véhicule
(prix neuf du modèle × décote selon l'âge, cf. estimate_acquisition).
La fiabilité et l'entretien sont ajoutés par reliability.enrich() ; le calcul
buy/sell complet (avec décote fine) vit dans tco.py.

⚠️ Tant que peu de passages sont accumulés, l'occupation (donc revenu/ROI) est
BRUITÉE. Le code est correct dès maintenant ; les CONCLUSIONS se fiabilisent avec
les jours de collecte. `min_passages` filtre les véhicules trop peu observés.
"""
from __future__ import annotations
import pandas as pd
import numpy as np

from features import (load_snapshots, collection_grid,
                      utilization_per_vehicle, rental_episodes)
import reliability


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


# ---------------------------------------------------------------------------
# COÛT D'ACQUISITION — estimation paramétrique (prix neuf × décote selon l'âge)
# ---------------------------------------------------------------------------
# L'Argus n'a pas d'API gratuite : on estime le prix d'occasion par
# prix_neuf(modèle) × rétention(âge). C'est une APPROXIMATION transparente et
# éditable, pas une cote officielle. Pour des cotes exactes -> source payante.

# prix neuf indicatif (€) par mot-clé modèle (1re correspondance gagne)
_NEW_PRICE_MODEL: list[tuple[tuple[str, ...], int]] = [
    (("master", "boxer", "ducato", "jumper", "movano", "daily", "crafter",
      "sprinter"), 40000),                                    # gros fourgons
    (("trafic", "jumpy", "expert", "vivaro", "transporter", "transit",
      "proace", "scudo", "talento", "vito"), 33000),          # fourgons moyens
    (("kangoo", "berlingo", "partner", "combo", "doblo", "caddy", "dokker",
      "nv200", "fiorino"), 23000),                            # ludospaces/petits utili
    (("5008", "espace", "scenic", "scénic", "touran", "zafira", "lodgy",
      "jogger"), 32000),                                      # familiales/monospaces
    (("3008", "tiguan", "qashqai", "kadjar", "duster", "rav4", "tucson",
      "koleos", "x3", "q5", "glc"), 30000),                   # SUV moyens
    (("2008", "captur", "juke", "t-roc", "t-cross", "mokka", "puma", "arona",
      "crossland", "c-hr", "kona", "x1", "q3", "gla"), 25000),# SUV compacts
    (("308", "megane", "mégane", "golf", "astra", "focus", "leon", "octavia",
      "civic", "a3", "corolla"), 27000),                      # compactes
    (("508", "passat", "mondeo", "insignia", "serie 3", "a4", "classe"),
     35000),                                                  # berlines
    (("208", "clio", "c3", "polo", "corsa", "fiesta", "ibiza", "yaris",
      "a1", "ds 3", "ds3"), 19000),                           # citadines
    (("108", "twingo", "aygo", "c1", "500", "panda", "up", "micra", "i10",
      "fabia", "sandero", "citigo", "celerio", "ypsilon", "punto", "fortwo",
      "spring", "zoe"), 15000),                               # mini-citadines
]
# prix neuf par défaut par segment si le modèle n'est pas listé
_NEW_PRICE_SEG = {
    "Utilitaire": 30000, "Citadine": 18000, "Berline": 30000, "SUV": 27000,
    "Minibus": 42000, "Familiale": 30000, "Cabriolet": 35000, "Autre": 22000,
}


def _new_price(make, model, segment) -> int:
    m = f"{make or ''} {model or ''}".lower()
    for keys, price in _NEW_PRICE_MODEL:
        if any(k in m for k in keys):
            return price
    return _NEW_PRICE_SEG.get(segment, 22000)


def estimate_acquisition(make, model, year, segment, ref_year: int) -> float:
    """Prix d'occasion estimé = prix_neuf × rétention(âge), plancher 2000 €.

    Rétention : -20 % la 1re année, puis -13 %/an (courbe de décote usuelle)."""
    new = _new_price(make, model, segment)
    age = 8 if year is None or pd.isna(year) else max(0, ref_year - int(year))
    retention = 1.0 if age == 0 else 0.80 * (0.87 ** (age - 1))
    return round(max(2000.0, new * max(retention, 0.12)), 0)


# ---------------------------------------------------------------------------
# 2. FEATURES PAR VÉHICULE
# ---------------------------------------------------------------------------
def build_features(snap: pd.DataFrame | None = None,
                   min_passages: int = 3) -> pd.DataFrame:
    """Une ligne par véhicule : identité, segment, prix, occupation, revenu, ROI.

    min_passages : ignore les véhicules vus à moins de N passages (bruit)."""
    if snap is None:
        snap = load_snapshots()
    grid = collection_grid()
    ref_year = int(pd.to_datetime(snap["snapshot_ts"]).max().year)

    util = utilization_per_vehicle(snap, grid=grid)         # occupation + épisodes
    util = util[(util["n_passages"] >= min_passages)
                & util["daily_rate"].notna()].copy()        # écarte prix/modèle manquants

    util["segment"] = [segment_of(mk, md)
                       for mk, md in zip(util["make"], util["model"])]
    # coût d'achat estimé par véhicule (prix neuf modèle × décote selon l'âge)
    util["cout_acquisition"] = [
        estimate_acquisition(mk, md, yr, seg, ref_year)
        for mk, md, yr, seg in zip(util["make"], util["model"],
                                   util["year"], util["segment"])]
    # revenu réalisé estimé + rentabilité du capital
    util["revenu_jour"] = util["taux_occupation"] * util["daily_rate"]
    util["revenu_annuel"] = util["revenu_jour"] * 365.0
    util["roi_annuel"] = util["revenu_annuel"] / util["cout_acquisition"]
    # enrichissement fiabilité + entretien -> rentabilité NETTE
    util = reliability.enrich(util, ref_year)
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
                entretien_moy=("entretien_annuel", "mean"),
                fiabilite=("fiabilite_score", "mean"),
                cout_acq=("cout_acquisition", "median"),
                roi_annuel=("roi_annuel", "mean"),
                roi_net=("roi_net", "mean"))
           .reset_index())
    agg = agg[agg["n_vehicules"] >= min_n]
    return agg.sort_values("roi_net", ascending=False).round(3)


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

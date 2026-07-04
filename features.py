"""
features.py — Moteur temporel GBFS Getaround
============================================

Transforme des SNAPSHOTS RÉPÉTÉS de la flotte (table vehicle_snapshots) en
SIGNAUX DE DEMANDE, de PRIX et de RENTABILITÉ réels.

Le principe (différent de l'ancien scraping calendrier) :
    Getaround retire du flux GBFS les voitures actuellement louées. Donc une
    LOCATION = un `listing_id` PRÉSENT à un passage puis ABSENT au suivant, et le
    RETOUR = sa réapparition. En reconstituant la présence de chaque véhicule sur
    la grille des passages, on mesure :
      - le taux d'occupation réel par véhicule / commune / modèle,
      - le nombre et la durée des épisodes de location,
      - un revenu réalisé estimé (jours loués × prix du véhicule),
      - donc un ratio de rentabilité par modèle, ancré sur de vraies locations.

⚠️ Caveat plafond : le flux Paris est limité à 1000 véhicules. Une voiture peut
donc disparaître non pas parce qu'elle est louée, mais parce qu'elle est poussée
hors du top-1000. `min_absence_snapshots` filtre ces disparitions d'un seul
passage ; augmente-le si le bruit de bord te gêne.

Ce module ne fait AUCUN réseau : il ne lit que ce que ingest.py a stocké.
"""
from __future__ import annotations
import sqlite3
import glob
from pathlib import Path
import pandas as pd
import numpy as np

from config import DB_PATH, DATA_DIR

# colonnes attendues (pour renvoyer un tableau vide bien formé si aucune donnée)
_COLS = ["snapshot_ts", "system_id", "listing_id", "vehicle_id", "commune",
         "lat", "lon", "is_reserved", "is_disabled", "current_range_meters",
         "vehicle_type_id", "make", "model", "year", "propulsion",
         "pricing_plan_id", "hourly_rate", "daily_rate", "rental_url"]


# --------------------------------------------------------------------------
# Chargement — source canonique = data/*.csv (committé), repli SQLite local
# --------------------------------------------------------------------------
def _csv_partitions():
    # partitions journalières AAAA-MM-JJ.csv (exclut runs.csv)
    pattern = "[0-9][0-9][0-9][0-9]-*.csv"
    return sorted(glob.glob(str(DATA_DIR / pattern)))


def load_snapshots(db_path=DB_PATH) -> pd.DataFrame:
    parts = _csv_partitions()
    if parts:
        df = pd.concat([pd.read_csv(p) for p in parts], ignore_index=True)
    elif Path(db_path).exists():                      # repli SQLite local
        con = sqlite3.connect(db_path)
        try:
            df = pd.read_sql("SELECT * FROM vehicle_snapshots", con)
        except Exception:                             # base sans la table
            df = pd.DataFrame(columns=_COLS)
        con.close()
    else:                                             # aucune donnée encore
        df = pd.DataFrame(columns=_COLS)
    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce")
    # clé d'identité stable : listing_id si dispo, sinon vehicle_id
    df["uid"] = df["listing_id"].fillna(df["vehicle_id"]) if len(df) else pd.Series(dtype=object)
    return df


def collection_grid(db_path=DB_PATH):
    """Liste autoritative des passages de collecte (depuis runs.csv / run_log).

    C'est la grille temporelle correcte : un véhicule ABSENT n'écrit aucune
    ligne, donc on ne peut pas déduire les passages des seuls véhicules vus.
    """
    runs = DATA_DIR / "runs.csv"
    if runs.exists():
        ts = pd.read_csv(runs, usecols=["snapshot_ts"])["snapshot_ts"]
    else:                                             # repli SQLite
        con = sqlite3.connect(db_path)
        ts = pd.read_sql("SELECT DISTINCT snapshot_ts FROM run_log", con)["snapshot_ts"]
        con.close()
    return np.sort(pd.to_datetime(ts.unique()))


# --------------------------------------------------------------------------
# 1. PRÉSENCE sur la grille des passages  (la base de tout)
# --------------------------------------------------------------------------
def presence_matrix(snap: pd.DataFrame, grid=None) -> pd.DataFrame:
    """Matrice booléenne uid × snapshot_ts : True = véhicule présent (donc
    LIBRE) à ce passage. Les trous (False) sont des périodes loué/retiré.

    grid : liste des passages de collecte. À fournir (via collection_grid())
    pour être robuste ; sinon déduite des véhicules vus (OK en prod où chaque
    passage a ~1000 véhicules, mais faux si un passage n'a aucun présent)."""
    if grid is None:
        grid = np.sort(snap["snapshot_ts"].unique())
    pres = (snap.assign(present=True)
                .pivot_table(index="uid", columns="snapshot_ts",
                             values="present", aggfunc="any", fill_value=False)
                .reindex(columns=grid, fill_value=False)
                .astype(bool))
    return pres


# --------------------------------------------------------------------------
# 2. ÉPISODES DE LOCATION  (le coeur du signal de demande)
# --------------------------------------------------------------------------
def rental_episodes(snap: pd.DataFrame,
                    min_absence_snapshots: int = 1,
                    grid=None) -> pd.DataFrame:
    """
    Un épisode = une plage où le véhicule DISPARAÎT du flux entre deux passages
    où il était présent (donc il est réapparu → on connaît le retour).

    Retourne 1 ligne par épisode : uid, commune, modèle, prix, début (dernier vu
    dispo), fin (1re réapparition), durée en heures. Les absences en fin de série
    (jamais réapparu) sont ignorées ici (censurées : loué long OU délisté).

    min_absence_snapshots : ignore les trous plus courts que N passages (filtre
    le bruit du plafond 1000 / hoquets de flux).
    """
    pres = presence_matrix(snap, grid=grid)
    grid = list(pres.columns)
    # métadonnées par uid (dernier snapshot connu)
    meta = (snap.sort_values("snapshot_ts")
                .groupby("uid")
                .agg(commune=("commune", "last"), make=("make", "last"),
                     model=("model", "last"), year=("year", "last"),
                     daily_rate=("daily_rate", "last"),
                     hourly_rate=("hourly_rate", "last")).reset_index())

    episodes = []
    arr = pres.to_numpy()
    uids = pres.index.to_numpy()
    for i, uid in enumerate(uids):
        row = arr[i]
        # parcourt les transitions present -> absent -> present
        t = 0
        n = len(row)
        while t < n:
            if row[t]:
                # cherche le prochain trou après une présence
                start = t
                j = t + 1
                while j < n and not row[j]:
                    j += 1
                gap_len = j - start - 1
                if gap_len >= min_absence_snapshots and j < n:
                    # trou borné des deux côtés -> vraie location
                    episodes.append(dict(
                        uid=uid,
                        start_ts=grid[start], end_ts=grid[j],
                        gap_snapshots=gap_len,
                        duration_h=(grid[j] - grid[start]).total_seconds() / 3600.0))
                t = j
            else:
                t += 1

    ep = pd.DataFrame(episodes)
    if ep.empty:
        return ep
    return ep.merge(meta, on="uid", how="left")


# --------------------------------------------------------------------------
# 3. OCCUPATION / UTILISATION par véhicule
# --------------------------------------------------------------------------
def utilization_per_vehicle(snap: pd.DataFrame, grid=None) -> pd.DataFrame:
    """Par véhicule : part des passages où il était ABSENT (loué), nb d'épisodes,
    prix. Occupation = temps loué / temps observé (proxy direct de la demande)."""
    pres = presence_matrix(snap, grid=grid)
    n_obs = pres.shape[1]
    absent = (~pres).sum(axis=1)
    ep = rental_episodes(snap, grid=grid)
    n_ep = (ep.groupby("uid").size() if not ep.empty
            else pd.Series(dtype=int)).rename("n_episodes")

    meta = (snap.sort_values("snapshot_ts").groupby("uid")
                .agg(commune=("commune", "last"), make=("make", "last"),
                     model=("model", "last"), year=("year", "last"),
                     daily_rate=("daily_rate", "last")))
    out = meta.join(pd.DataFrame({"n_passages": n_obs,
                                  "n_absent": absent})).join(n_ep)
    out["n_episodes"] = out["n_episodes"].fillna(0).astype(int)
    out["taux_occupation"] = out["n_absent"] / out["n_passages"]
    return out.reset_index().sort_values("taux_occupation", ascending=False)


# --------------------------------------------------------------------------
# 4. DYNAMIQUE DE PRIX par véhicule
# --------------------------------------------------------------------------
def price_dynamics(snap: pd.DataFrame) -> pd.DataFrame:
    """Par véhicule : min/max/dernier prix jour observé et amplitude. Révèle si
    Getaround / l'hôte pratique du pricing dynamique sur la période."""
    g = (snap.dropna(subset=["daily_rate"]).groupby("uid")
             .agg(commune=("commune", "last"), make=("make", "last"),
                  model=("model", "last"),
                  prix_min=("daily_rate", "min"), prix_max=("daily_rate", "max"),
                  prix_dernier=("daily_rate", "last"),
                  n_prix=("daily_rate", "nunique")))
    g["amplitude_eur"] = g["prix_max"] - g["prix_min"]
    g["prix_variable"] = g["n_prix"] > 1
    return g.reset_index().sort_values("amplitude_eur", ascending=False)


# --------------------------------------------------------------------------
# 5. PHOTO DE MARCHÉ (dernier passage) — étude descriptive
# --------------------------------------------------------------------------
def market_snapshot(snap: pd.DataFrame) -> dict:
    """Synthèse descriptive au dernier passage : taille de flotte, répartition
    par commune, marque, motorisation, distribution de prix."""
    last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()]
    return dict(
        horodatage=str(last["snapshot_ts"].iloc[0]),
        n_vehicules=len(last),
        n_communes=last["commune"].nunique(),
        par_commune=last["commune"].value_counts().head(15),
        par_marque=last["make"].value_counts().head(10),
        par_motorisation=last["propulsion"].value_counts(),
        prix_jour=last["daily_rate"].describe(),
    )


# --------------------------------------------------------------------------
# 6. RENTABILITÉ par catégorie/modèle (revenu réalisé mesuré)
# --------------------------------------------------------------------------
def rentability_by_group(snap: pd.DataFrame, by: str = "make",
                         acquisition_cost: dict | None = None) -> pd.DataFrame:
    """
    Revenu réalisé estimé par groupe (make/model/commune…) :
        occupation moyenne × prix jour moyen × 365 = revenu annualisé / véhicule.
    Si acquisition_cost est fourni ({groupe: coût €}), calcule le ratio
    revenu_annuel / capital — le vrai signal d'investissement.
    """
    util = utilization_per_vehicle(snap)
    agg = (util.groupby(by)
               .agg(n_vehicules=("uid", "nunique"),
                    occupation_moy=("taux_occupation", "mean"),
                    prix_jour_moy=("daily_rate", "mean"),
                    episodes_moy=("n_episodes", "mean")).reset_index())
    agg["revenu_annuel_estime"] = (agg["occupation_moy"]
                                   * agg["prix_jour_moy"] * 365.0)
    if acquisition_cost:
        agg["cout_acquisition"] = agg[by].map(acquisition_cost)
        agg["ratio_rentabilite"] = (agg["revenu_annuel_estime"]
                                    / agg["cout_acquisition"])
    return agg.sort_values("revenu_annuel_estime", ascending=False).round(3)


# --------------------------------------------------------------------------
# DÉMO
# --------------------------------------------------------------------------
def _demo():
    if DB_PATH.exists():
        snap = load_snapshots()
        n_ts = snap["snapshot_ts"].nunique()
        print(f"Base réelle : {snap['uid'].nunique()} véhicules, "
              f"{n_ts} passage(s).")
        ms = market_snapshot(snap)
        print("\n=== PHOTO DE MARCHÉ (dernier passage) ===")
        print(f"{ms['n_vehicules']} véhicules sur {ms['n_communes']} communes")
        print("\nTop communes :\n", ms["par_commune"].head(8).to_string())
        print("\nPrix/jour (€) :\n", ms["prix_jour"].round(1).to_string())
        if n_ts < 2:
            print("\n(Un seul passage : relance ingest.py régulièrement pour "
                  "débloquer la détection de locations et l'occupation.)")
            return
        print("\n=== OCCUPATION (top véhicules loués) ===")
        print(utilization_per_vehicle(snap).head(8)
              [["uid", "commune", "make", "model", "taux_occupation",
                "n_episodes", "daily_rate"]].to_string(index=False))
        print("\n=== RENTABILITÉ PAR MARQUE ===")
        print(rentability_by_group(snap, by="make").head(10).to_string(index=False))
    else:
        print("Pas de base encore. Lance d'abord :  python ingest.py")


if __name__ == "__main__":
    _demo()

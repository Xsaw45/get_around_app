"""
export_ml.py — Fabrique les datasets ML depuis la base de snapshots
===================================================================

Écrit dans ./exports/ des fichiers Parquet propres, prêts pour l'analyse et
l'entraînement de modèles. Rejoue-le quand tu veux régénérer une photo figée du
dataset (la base sqlite, elle, continue de grossir à chaque passage).

Fichiers produits :
  snapshots.parquet     — table brute (1 ligne / véhicule / passage)
  vehicle_daily.parquet — 1 ligne / (véhicule, jour) : occupation, prix, modèle
                          -> cible pour prédiction demande & scoring rentabilité
  market_daily.parquet  — 1 ligne / (jour, commune) : taille flotte, prix médian
                          -> étude de marché & saisonnalité
"""
from __future__ import annotations
import pandas as pd

from config import EXPORT_DIR
from features import load_snapshots, presence_matrix


def build():
    EXPORT_DIR.mkdir(exist_ok=True)
    snap = load_snapshots()
    if snap.empty:
        print("Base vide — lance d'abord ingest.py.")
        return

    # 1) brut
    snap.to_parquet(EXPORT_DIR / "snapshots.parquet", index=False)

    snap["day"] = snap["snapshot_ts"].dt.floor("D")

    # 2) agrégat quotidien par véhicule
    #    occupation du jour = part des passages du jour où le véhicule est ABSENT.
    pres = presence_matrix(snap)                      # uid × ts (True=présent)
    long = (pres.stack().rename("present").reset_index())
    long["day"] = long["snapshot_ts"].dt.floor("D")
    vday = (long.groupby(["uid", "day"])
                .agg(n_passages=("present", "size"),
                     n_present=("present", "sum")).reset_index())
    vday["n_absent"] = vday["n_passages"] - vday["n_present"]
    vday["taux_occupation"] = vday["n_absent"] / vday["n_passages"]
    # rattache modèle/commune/prix (dernier connu dans le jour)
    meta = (snap.sort_values("snapshot_ts")
                .groupby(["uid", "day"])
                .agg(commune=("commune", "last"), make=("make", "last"),
                     model=("model", "last"), year=("year", "last"),
                     propulsion=("propulsion", "last"),
                     daily_rate=("daily_rate", "last"),
                     hourly_rate=("hourly_rate", "last")).reset_index())
    vday = vday.merge(meta, on=["uid", "day"], how="left")
    vday.to_parquet(EXPORT_DIR / "vehicle_daily.parquet", index=False)

    # 3) marché par jour × commune
    mkt = (snap.groupby(["day", "commune"])
               .agg(n_vehicules=("uid", "nunique"),
                    prix_median=("daily_rate", "median"),
                    prix_moyen=("daily_rate", "mean")).reset_index())
    mkt.to_parquet(EXPORT_DIR / "market_daily.parquet", index=False)

    print(f"Exports écrits dans {EXPORT_DIR}/ :")
    print(f"  snapshots.parquet      {len(snap):>7} lignes")
    print(f"  vehicle_daily.parquet  {len(vday):>7} lignes")
    print(f"  market_daily.parquet   {len(mkt):>7} lignes")


if __name__ == "__main__":
    build()

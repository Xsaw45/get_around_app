"""
webapp/data.py — payloads JSON pour le dashboard
=================================================

Construit les données affichées par le frontend en import DIRECT des modules
d'analyse existants (features.py, pipeline.py, ml.py) — même logique que ce qui
tourne en CLI, pas de duplication ni de parsing de sortie texte.

Un petit cache mémoire (TTL + invalidation explicite après un run de pipeline
réussi, voir `invalidate_cache()`) évite de relire les CSV de `data/` (plusieurs
centaines de Mo) à chaque requête de graphique.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from config import DATA_DIR
import features
import pipeline as pipeline_mod
import ml as ml_mod

_CACHE_TTL = 300  # secondes
_cache: dict[str, tuple[float, object]] = {}


def invalidate_cache() -> None:
    _cache.clear()


def _cached(key, build):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = build()
    _cache[key] = (now, value)
    return value


def _freshness(last_ts) -> dict:
    """Même calcul 🟢/🟠/🔴 que analyze.py:build(), réutilisé ici plutôt que
    redupliqué."""
    if last_ts is None or pd.isna(last_ts):
        return {"state": "red", "label": "aucune donnée", "age_hours": None,
                "last_pass": None}
    last = pd.Timestamp(last_ts).to_pydatetime()
    age_h = (dt.datetime.now() - last).total_seconds() / 3600
    if age_h < 2:
        state, label = "green", "à jour"
    elif age_h < 24:
        state, label = "orange", "un peu vieux"
    else:
        state, label = "red", "arrêté ?"
    return {"state": state, "label": label, "age_hours": round(age_h, 1),
            "last_pass": last.isoformat()}


def collector_status() -> dict:
    """Fraîcheur de la collecte auto, lue depuis runs.csv seul (léger,
    indépendant du chargement complet des snapshots)."""
    def build():
        runs = DATA_DIR / "runs.csv"
        if not runs.exists():
            return _freshness(None)
        last_ts = pd.to_datetime(
            pd.read_csv(runs, usecols=["snapshot_ts"])["snapshot_ts"]).max()
        return _freshness(last_ts)
    return _cached("collector_status", build)


def _histogram(series: pd.Series, bins: int = 20) -> list[dict]:
    if series.empty:
        return []
    counts, edges = np.histogram(series, bins=bins)
    return [{"x": float((edges[i] + edges[i + 1]) / 2), "count": int(counts[i])}
            for i in range(len(counts))]


# ---------------------------------------------------------------------------
# Accesseurs mis en cache, réutilisés par plusieurs endpoints (dont
# webapp/simulate.py) pour éviter de relire les ~750 Mo de CSV à chaque appel.
# ---------------------------------------------------------------------------
def load_snapshots_cached() -> pd.DataFrame:
    return _cached("snapshots_raw", features.load_snapshots)


def features_cached() -> pd.DataFrame:
    return _cached("features_full", lambda: pipeline_mod.build_features(load_snapshots_cached()))


def utilization_cached() -> pd.DataFrame:
    def build():
        util = features.utilization_per_vehicle(load_snapshots_cached())
        util["segment"] = [pipeline_mod.segment_of(mk, md)
                           for mk, md in zip(util["make"], util["model"])]
        return util
    return _cached("utilization", build)


def _investment_occupancy(snap: pd.DataFrame, min_absence_snapshots: int = 2) -> pd.DataFrame:
    """Occupation par véhicule robuste au bruit court, pour le simulateur
    d'investissement (webapp/simulate.py). Diffère de
    `features.utilization_per_vehicle` sur deux points :
      - ne compte comme location que les absences d'au moins
        `min_absence_snapshots` passages CONSÉCUTIFS (filtre les blips d'un
        seul passage : glitch réseau, effet de classement de l'API — cf.
        `capped_systems`, mais le bruit existe aussi hors zone plafonnée) ;
      - rapporte la durée cumulée des épisodes qualifiés à la fenêtre
        d'observation PROPRE à chaque véhicule (premier -> dernier passage où
        il a été vu), pas à la grille globale ni au simple ratio de snapshots
        absents — un véhicule récemment apparu n'est pas pénalisé.
    """
    pres = features.presence_matrix(snap)
    grid = list(pres.columns)
    arr = pres.to_numpy()

    spans = []
    for i, uid in enumerate(pres.index):
        seen = np.flatnonzero(arr[i])
        if len(seen) < 2:
            continue
        span_h = (grid[seen[-1]] - grid[seen[0]]).total_seconds() / 3600.0
        if span_h > 0:
            spans.append({"uid": uid, "span_h": span_h})
    span_df = pd.DataFrame(spans)
    if span_df.empty:
        return pd.DataFrame(columns=["uid", "occupation_investissement", "n_episodes_qualifies"])

    ep = features.rental_episodes(snap, min_absence_snapshots=min_absence_snapshots, grid=grid)
    if ep.empty:
        occ = pd.DataFrame({"uid": span_df["uid"], "occ_hours": 0.0, "n_episodes_qualifies": 0})
    else:
        occ = (ep.groupby("uid")["duration_h"]
                 .agg(occ_hours="sum", n_episodes_qualifies="count")
                 .reset_index())

    out = span_df.merge(occ, on="uid", how="left")
    out["occ_hours"] = out["occ_hours"].fillna(0.0)
    out["n_episodes_qualifies"] = out["n_episodes_qualifies"].fillna(0).astype(int)
    out["occupation_investissement"] = (out["occ_hours"] / out["span_h"]).clip(0, 1)
    return out[["uid", "occupation_investissement", "n_episodes_qualifies"]]


def investment_utilization_cached() -> pd.DataFrame:
    """Occupation investissement (voir _investment_occupancy) + segment/make/
    model/prix, mise en cache comme le reste."""
    def build():
        occ = _investment_occupancy(load_snapshots_cached())
        base = utilization_cached()[["uid", "make", "model", "segment", "daily_rate"]]
        return occ.merge(base, on="uid", how="left")
    return _cached("investment_utilization", build)


def capped_systems() -> set[str]:
    """Systèmes GBFS où l'API plafonne le nombre de véhicules renvoyés (n_seen
    quasi constant à chaque passage, cf. data/runs.csv : 'paris' est à
    exactement 1000/1000 sur 2334 passages, std=0 — contre une variance
    naturelle de ~1-2 véhicules pour meaux/chessy/serris/torcy). Dans ces
    zones, une disparition du flux peut être un effet de classement de l'API
    plutôt qu'une vraie location : l'occupation mesurée y est peu fiable."""
    def build():
        runs = DATA_DIR / "runs.csv"
        if not runs.exists():
            return set()
        df = pd.read_csv(runs, usecols=["system_id", "n_seen"])
        g = df.groupby("system_id")["n_seen"].agg(["count", "std"])
        capped = g[(g["count"] >= 5) & (g["std"].fillna(0) < 0.5)]
        return set(capped.index)
    return _cached("capped_systems", build)


def fleet_points() -> list[dict]:
    """Position + segment de chaque véhicule au dernier passage — pour la carte
    du simulateur."""
    def build():
        snap = load_snapshots_cached()
        if snap.empty:
            return []
        last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()]
        segs = [pipeline_mod.segment_of(mk, md) for mk, md in zip(last["make"], last["model"])]
        pts = (pd.DataFrame({"lat": last["lat"], "lon": last["lon"], "segment": segs})
                 .dropna(subset=["lat", "lon"]))
        return json.loads(pts.round(5).to_json(orient="records"))
    return _cached("fleet_points", build)


def market_summary() -> dict:
    def build():
        snap = load_snapshots_cached()
        if snap.empty:
            return {"empty": True}
        ms = features.market_snapshot(snap)
        last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()]
        prix = last["daily_rate"].dropna()
        return {
            "empty": False,
            "freshness": _freshness(snap["snapshot_ts"].max()),
            "n_vehicules": int(ms["n_vehicules"]),
            "n_communes": int(ms["n_communes"]),
            "n_passages": int(snap["snapshot_ts"].nunique()),
            "prix": {"median": float(prix.median()), "min": float(prix.min()),
                     "max": float(prix.max())},
            "par_commune": [{"label": k, "value": int(v)}
                            for k, v in ms["par_commune"].head(12).items()],
            "par_marque": [{"label": k, "value": int(v)}
                           for k, v in ms["par_marque"].head(10).items()],
            "par_motorisation": [{"label": k, "value": int(v)}
                                 for k, v in ms["par_motorisation"].items()],
            "prix_histogramme": _histogram(prix),
        }
    return _cached("market_summary", build)


def rentability(by: str = "segment") -> list[dict]:
    def build():
        snap = load_snapshots_cached()
        if snap.empty:
            return []
        ranked = pipeline_mod.rank_by(features_cached(), by=by)
        if by == "model":
            ranked = ranked.head(12)
        return ranked.round(3).to_dict(orient="records")
    return _cached(f"rentability_{by}", build)


def ml_insights() -> dict:
    def build():
        snap = load_snapshots_cached()
        if snap.empty or snap["snapshot_ts"].nunique() < 2:
            return {"empty": True}
        try:
            return {"empty": False, **ml_mod.run_full()}
        except Exception as exc:                      # données encore trop rares
            return {"empty": True, "error": str(exc)}
    return _cached("ml_insights", build)


def activity() -> list[dict]:
    """Passages de collecte par jour (data/runs.csv) — activité dans le temps."""
    def build():
        runs = DATA_DIR / "runs.csv"
        if not runs.exists():
            return []
        df = pd.read_csv(runs, parse_dates=["snapshot_ts"])
        df["date"] = df["snapshot_ts"].dt.date.astype(str)
        daily = (df.groupby("date")
                   .agg(passages=("snapshot_ts", "nunique"),
                        vehicules_vus=("n_kept", "sum"))
                   .reset_index())
        return daily.to_dict(orient="records")
    return _cached("activity", build)

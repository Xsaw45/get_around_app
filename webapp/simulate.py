"""
webapp/simulate.py — « et si j'achetais ce véhicule ici ? »
=============================================================

Estime la rentabilité d'un véhicule hypothétique (marque/modèle/motorisation)
à un endroit donné, à partir des comparables mesurés dans un rayon (km) autour
d'un point GPS. Nécessaire car le champ `commune` ne descend pas à
l'arrondissement — Paris intra-muros est une seule commune dans les données,
trop grossière pour distinguer un quartier (ex. Nation) du reste de la ville.

Réutilise telles quelles les briques existantes :
  - pipeline.segment_of        : classification marque/modèle -> segment
  - tco.breakeven_occupancy / tco.recommend : seuil de rentabilité + fenêtre
    d'achat/revente (mêmes hypothèses que la démo CLI de tco.py)

L'occupation utilisée ici n'est PAS le ratio brut de features.py
(`taux_occupation` = n_absent/n_passages, qui compte à tort tout véhicule
délisté en cours de route comme "loué en continu" jusqu'à la fin de la
collecte) mais `data.investment_utilization_cached()` — durée des épisodes
réellement bouclés (présent -> absent -> réapparu), rapportée à la fenêtre
d'observation propre à chaque véhicule. Plus conservatrice, plus défendable
pour une décision d'investissement.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline as pipeline_mod
import tco

from webapp import data as data_mod

MIN_LOCAL_COMPS = 5   # sous ce seuil, on retombe sur la moyenne IDF du segment


def _local_mask(df, lat: float, lon: float, radius_km: float):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return df["lat"].between(lat - dlat, lat + dlat) & df["lon"].between(lon - dlon, lon + dlon)


def _occupancy_ci(values) -> dict:
    """IC90% approximatif (approximation normale via pandas.Series.sem(), pas
    de dépendance scipy) autour d'une moyenne d'occupation locale. Purement
    indicatif — n souvent petit sur 3 semaines de données ; sert à montrer la
    marge d'incertitude plutôt qu'un chiffre unique trompeur."""
    n = int(values.count())
    mean = float(values.mean()) if n else 0.0
    if n <= 1:
        return {"mean": round(mean, 3), "low": round(mean, 3), "high": round(mean, 3), "n": n}
    se = float(values.sem())
    half = 1.645 * se
    return {"mean": round(mean, 3), "low": round(max(0.0, mean - half), 3),
            "high": round(min(1.0, mean + half), 3), "n": n}


def _cap_warning(zone) -> str | None:
    """Signale si la zone est couverte par un système GBFS plafonné (nombre de
    véhicules renvoyés constant à chaque passage, cf. data_mod.capped_systems)
    — dans ce cas une disparition du flux peut être un effet de classement de
    l'API plutôt qu'une vraie location, et l'occupation mesurée n'est pas
    fiable comme signal de demande."""
    if "system_id" not in zone.columns:
        return None
    zone_systems = set(zone["system_id"].dropna().unique())
    capped_here = zone_systems & data_mod.capped_systems()
    if not capped_here:
        return None
    names = ", ".join(sorted(capped_here))
    return (f"Zone couverte par le système GBFS « {names} », plafonné à un nombre fixe "
           f"de véhicules affichés à chaque passage (jamais la flotte réelle complète). "
           f"Une voiture qui disparaît du flux peut donc juste sortir du classement de "
           f"l'API, pas forcément être louée : l'occupation mesurée ici est peu fiable "
           f"comme signal de demande.")


def simulate(make: str, model: str, propulsion: str, lat: float, lon: float,
            radius_km: float = 1.2, daily_rate: float | None = None) -> dict:
    segment = pipeline_mod.segment_of(make, model)
    snap = data_mod.load_snapshots_cached()
    if snap.empty:
        return {"error": "Aucune donnée collectée pour l'instant — lance le pipeline."}

    last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()].copy()
    zone = last[_local_mask(last, lat, lon, radius_km)].copy()
    zone["segment"] = [pipeline_mod.segment_of(mk, md) for mk, md in zip(zone["make"], zone["model"])]
    zone_seg = zone[zone["segment"] == segment]
    cap_warning = _cap_warning(zone)

    # occupation « investissement » (durée-based, censure correctement les
    # véhicules délistés, filtre le bruit court) : mesurée localement si assez
    # de comparables, sinon repli IDF — voir data.investment_utilization_cached
    util = data_mod.investment_utilization_cached()
    util_local = util[util["uid"].isin(set(zone_seg["uid"]))]
    used_local = len(util_local) >= MIN_LOCAL_COMPS
    if used_local:
        ci = _occupancy_ci(util_local["occupation_investissement"])
        occ_source = f"mesurée localement sur {ci['n']} comparables ({segment.lower()}) dans le rayon"
    else:
        seg_all = util[util["segment"] == segment]
        ci = _occupancy_ci(seg_all["occupation_investissement"])
        occ_source = (f"moyenne IDF du segment {segment} "
                      f"(trop peu de comparables locaux : {len(util_local)})")
    occupancy = ci["mean"]

    # prix : celui saisi par l'utilisateur, sinon moyenne locale, sinon moyenne IDF
    price_source = "prix saisi par l'utilisateur"
    if daily_rate is None:
        if len(zone_seg) >= 3:
            daily_rate = float(zone_seg["daily_rate"].dropna().mean())
            price_source = f"moyenne locale du segment {segment}, {len(zone_seg)} véhicules"
        else:
            last_seg = last.assign(segment=[pipeline_mod.segment_of(mk, md)
                                            for mk, md in zip(last["make"], last["model"])])
            prices = last_seg.loc[last_seg["segment"] == segment, "daily_rate"].dropna()
            daily_rate = float(prices.mean()) if len(prices) else 40.0
            price_source = f"moyenne IDF du segment {segment} (trop peu de comparables locaux)"

    # seuil indicatif (hypothèse simple achat à 4 ans / détention 5 ans) ; le
    # verdict affiché, lui, suit rec["rentable"] qui balaie tous les âges 0-15
    # (tco.recommend) — les deux peuvent diverger, ex. une fenêtre rentable
    # plus tard dans la vie du véhicule alors que 4-9 ans ne l'est pas.
    breakeven = tco.breakeven_occupancy(segment, propulsion, daily_rate, make, model)
    rec = tco.recommend(segment, propulsion, occupancy, daily_rate, make, model)
    table = json.loads(rec["table"].round(0).to_json(orient="records"))

    # verdict fiable seulement si la mesure n'est pas structurellement
    # compromise (zone plafonnée ou repli IDF faute de comparables locaux) —
    # sinon "indéterminé" plutôt qu'un vert/rouge trompeur
    verdict_reliable = used_local and not cap_warning
    rentable = bool(rec["rentable"]) if verdict_reliable else None
    verdict_note = None
    if not verdict_reliable:
        reasons = []
        if cap_warning:
            reasons.append("zone plafonnée")
        if not used_local:
            reasons.append("pas assez de comparables locaux (repli IDF)")
        verdict_note = "Verdict indéterminé : " + " et ".join(reasons) + "."

    return {
        "segment": segment,
        "n_local_total": int(len(zone)),
        "n_local_segment": int(len(zone_seg)),
        "occupancy": occupancy,
        "occupancy_ci": {"low": ci["low"], "high": ci["high"], "n": ci["n"]},
        "occupancy_source": occ_source,
        "daily_rate": round(daily_rate, 1),
        "price_source": price_source,
        "breakeven_occupancy": breakeven,
        "rentable": rentable,
        "verdict_reliable": verdict_reliable,
        "verdict_note": verdict_note,
        "message": rec["message"],
        "table": table,
        "cap_warning": cap_warning,
    }


def rank_models_for_location(lat: float, lon: float, radius_km: float = 1.2,
                             top_n: int = 15) -> dict:
    """Classe les modèles RÉELS de la flotte par rentabilité estimée à un
    endroit donné. Contrairement à simulate(), ne retombe JAMAIS sur une
    moyenne IDF : un segment sans assez de comparables locaux est explicitement
    exclu du classement plutôt que de fausser le résultat avec une moyenne
    nationale déguisée en signal local (cf. l'écart mesuré Nation vs IDF sur la
    Yaris — le repli aurait autrement dominé le classement)."""
    snap = data_mod.load_snapshots_cached()
    if snap.empty:
        return {"error": "Aucune donnée collectée pour l'instant — lance le pipeline."}

    last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()].copy()
    zone = last[_local_mask(last, lat, lon, radius_km)].copy()
    zone["segment"] = [pipeline_mod.segment_of(mk, md) for mk, md in zip(zone["make"], zone["model"])]

    util = data_mod.investment_utilization_cached()
    cap_warning = _cap_warning(zone)
    verdict_reliable = not cap_warning   # les segments sans assez de comparables sont déjà exclus ci-dessous

    seg_stats: dict[str, dict] = {}
    excluded = []
    for seg in sorted(util["segment"].unique()):
        zone_seg = zone[zone["segment"] == seg]
        util_local = util[util["uid"].isin(set(zone_seg["uid"]))]
        if len(util_local) < MIN_LOCAL_COMPS:
            excluded.append({"segment": seg, "n_local": int(len(util_local))})
            continue
        prices = zone_seg["daily_rate"].dropna()
        if prices.empty:
            excluded.append({"segment": seg, "n_local": int(len(util_local))})
            continue
        ci = _occupancy_ci(util_local["occupation_investissement"])
        seg_stats[seg] = {"occupancy_ci": ci, "daily_rate": float(prices.mean())}

    if not seg_stats:
        return {"n_local_total": int(len(zone)), "segment_stats": {}, "excluded_segments": excluded,
                "models": [], "cap_warning": cap_warning, "verdict_reliable": verdict_reliable}

    feats = data_mod.features_cached()
    models = (feats.assign(mm=feats["make"].fillna("?") + " " + feats["model"].fillna("?"))
                   .groupby(["mm", "make", "model", "segment", "propulsion"])
                   .agg(n=("uid", "nunique")).reset_index())
    models = models[(models["n"] >= 5) & (models["segment"].isin(seg_stats))]
    models = models.sort_values("n", ascending=False).drop_duplicates(subset=["mm"])

    rows = []
    for _, r in models.iterrows():
        st = seg_stats[r["segment"]]
        occ = st["occupancy_ci"]["mean"]
        rec = tco.recommend(r["segment"], r["propulsion"], occ, st["daily_rate"], r["make"], r["model"])
        rows.append({
            "model": r["mm"], "segment": r["segment"], "propulsion": r["propulsion"],
            "n_idf": int(r["n"]), "occupancy": occ,
            "daily_rate": round(st["daily_rate"], 1),
            "rentable": bool(rec["rentable"]) if verdict_reliable else None,
            "profit_total": rec.get("profit_total"), "acheter_age": rec.get("acheter_age"),
            "vendre_age": rec.get("vendre_age"), "message": rec["message"],
        })
    rows.sort(key=lambda d: d["profit_total"] if d["profit_total"] is not None else -1e18, reverse=True)

    return {
        "n_local_total": int(len(zone)),
        "segment_stats": {seg: {"occupancy": v["occupancy_ci"]["mean"],
                                "occupancy_ci": {"low": v["occupancy_ci"]["low"],
                                                "high": v["occupancy_ci"]["high"]},
                                "daily_rate": round(v["daily_rate"], 1),
                                "n_local": v["occupancy_ci"]["n"]}
                          for seg, v in seg_stats.items()},
        "excluded_segments": excluded,
        "models": rows[:top_n],
        "cap_warning": cap_warning,
        "verdict_reliable": verdict_reliable,
    }

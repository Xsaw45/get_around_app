"""
ml.py — Modèle de demande : prédire l'occupation & comprendre ce qui la drive
============================================================================

Objectif : au-delà du classement descriptif, MODÉLISER l'occupation d'un véhicule
en fonction de ses caractéristiques, pour (a) comprendre *ce qui rend rentable*
(prix, segment, zone, âge, densité d'offre…) et (b) prédire l'occupation d'un
profil non encore observé.

Démarche (cf. la discussion méthodo) :
  1. Baseline naïf (moyenne) et par segment → le seuil à battre.
  2. Régression linéaire régularisée (Ridge) → effets *propres* et lisibles.
  3. Gradient boosting (HistGradientBoosting) → non-linéarités + interactions.
  4. Importance de permutation + dépendances partielles → le "pourquoi".

Cible y = taux_occupation (0-1). Une ligne = un véhicule (agrégé sur tous les
passages), donc pas de fuite temporelle : KFold standard suffit.

Features de densité, toutes calculées par RAYON GPS (KD-tree) plutôt que par
commune : `commune` est un bucket administratif grossier (tout Paris
intramuros est UNE SEULE commune dans les données — biais découvert en
construisant le simulateur d'investissement, cf. webapp/simulate.py) qui
sous-estime la variation réelle à l'intérieur d'une même ville.
  - `densite_commune` / `densite_segment` = nb de véhicules (tous / même
    segment) dans un rayon de 1 km → capture la SURREPRÉSENTATION locale
    (l'hypothèse Kangoo Express), à l'échelle du quartier plutôt que de la ville.
  - `densite_yespark` = nb de parkings YesPark (location de place au mois)
    dans un rayon de 1 km → proxy de densité de stationnement privé à
    proximité (cf. yespark.py).
  - `densite_transport` = nb d'arrêts de transport LOURD (métro/RER/tram/train,
    hors bus) dans un rayon de 500 m → accessibilité en transport en commun
    (cf. idfm.py).
Ces trois dernières sont optionnelles : restent à 0 partout tant que le
fichier `data/*.csv` correspondant n'a pas été généré (pas de dépendance dure).

⚠️ Données encore modestes (~1300 véhicules, occupation en cours de stabilisation).
Le but ici est la MÉTHODE + le signal directionnel, pas des coefficients définitifs.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.model_selection import KFold
from sklearn.inspection import permutation_importance, partial_dependence
from sklearn.metrics import mean_absolute_error, r2_score

from config import DATA_DIR
from features import load_snapshots
from pipeline import build_features

CAT = ["segment", "propulsion", "make"]
NUM = ["age", "daily_rate", "fiabilite_score", "densite_commune", "densite_segment",
      "densite_yespark", "densite_transport"]

COMPETITION_RADIUS_KM = 1.0
YESPARK_RADIUS_KM = 1.0
TRANSIT_RADIUS_KM = 0.5                              # distance de marche raisonnable


def _project_xy(lat: pd.Series, lon: pd.Series, lat0: float) -> np.ndarray:
    """Projection équirectangulaire approx degrés -> km (précision suffisante
    à l'échelle IDF, cf. même approche dans webapp/simulate.py)."""
    km_lat, km_lon = 111.0, 111.0 * np.cos(np.radians(lat0))
    return np.column_stack([lon.fillna(0).to_numpy() * km_lon,
                            lat.fillna(0).to_numpy() * km_lat])


def _density_from_points(df: pd.DataFrame, points_lat: pd.Series, points_lon: pd.Series,
                         radius_km: float) -> pd.Series:
    """Nb de points externes (points_lat/lon) dans un rayon (km) de chaque
    véhicule de df (colonnes lat/lon)."""
    zeros = pd.Series(0, index=df.index, dtype=int)
    if "lat" not in df.columns or df[["lat", "lon"]].dropna().empty or len(points_lat) == 0:
        return zeros
    from scipy.spatial import cKDTree
    lat0 = df["lat"].dropna().mean()
    veh_xy = _project_xy(df["lat"], df["lon"], lat0)
    pts_xy = _project_xy(points_lat.reset_index(drop=True), points_lon.reset_index(drop=True), lat0)
    counts = pd.Series(cKDTree(pts_xy).query_ball_point(veh_xy, r=radius_km, return_length=True),
                       index=df.index)
    counts[df["lat"].isna() | df["lon"].isna()] = 0
    return counts.astype(int)


def _yespark_density(df: pd.DataFrame, radius_km: float = YESPARK_RADIUS_KM) -> pd.Series:
    """Nombre de parkings YesPark dans un rayon (km) de chaque véhicule (voir
    yespark.py). Renvoie 0 partout si data/yespark_parkings.csv n'existe pas
    encore — feature optionnelle, pas de dépendance dure."""
    path = DATA_DIR / "yespark_parkings.csv"
    if not path.exists():
        return pd.Series(0, index=df.index, dtype=int)
    yp = pd.read_csv(path).dropna(subset=["lat", "lon"])
    return _density_from_points(df, yp["lat"], yp["lon"], radius_km)


def _transit_density(df: pd.DataFrame, radius_km: float = TRANSIT_RADIUS_KM) -> pd.Series:
    """Nombre d'arrêts de transport lourd dans un rayon (km) de chaque
    véhicule (voir idfm.py). Renvoie 0 partout si data/idfm_arrets.csv
    n'existe pas encore — feature optionnelle, pas de dépendance dure."""
    path = DATA_DIR / "idfm_arrets.csv"
    if not path.exists():
        return pd.Series(0, index=df.index, dtype=int)
    st = pd.read_csv(path).dropna(subset=["lat", "lon"])
    return _density_from_points(df, st["lat"], st["lon"], radius_km)


def _local_densities(df: pd.DataFrame,
                     radius_km: float = COMPETITION_RADIUS_KM) -> tuple[pd.Series, pd.Series]:
    """Densité RÉELLE (rayon GPS) de véhicules à proximité — remplace l'ancien
    calcul par commune (bucket administratif grossier). Renvoie
    (densite_commune, densite_segment) : nb de véhicules dans le rayon, tous
    segments confondus / du même segment (hors le véhicule lui-même)."""
    dens_all = pd.Series(0, index=df.index, dtype=int)
    dens_seg = pd.Series(0, index=df.index, dtype=int)
    if "lat" not in df.columns:
        return dens_all, dens_seg
    valid = df[["lat", "lon"]].notna().all(axis=1)
    if valid.sum() < 2:
        return dens_all, dens_seg

    from scipy.spatial import cKDTree
    lat0 = df.loc[valid, "lat"].mean()
    xy = _project_xy(df["lat"], df["lon"], lat0)

    pos_valid = np.flatnonzero(valid.to_numpy())
    tree_all = cKDTree(xy[pos_valid])
    counts_all = tree_all.query_ball_point(xy[pos_valid], r=radius_km, return_length=True)
    dens_all.iloc[pos_valid] = counts_all - 1            # exclut soi-même

    for _, sub in df[valid].groupby("segment"):
        pos = df.index.get_indexer(sub.index)
        tree_seg = cKDTree(xy[pos])
        counts_seg = tree_seg.query_ball_point(xy[pos], r=radius_km, return_length=True)
        dens_seg.iloc[pos] = counts_seg - 1

    return dens_all, dens_seg


# ---------------------------------------------------------------------------
# Préparation des données
# ---------------------------------------------------------------------------
def prepare(min_passages: int = 3):
    feats = build_features(load_snapshots(), min_passages=min_passages)
    df = feats.copy()
    df["make"] = df["make"].fillna("?")
    # feature d'offre locale (concurrence), rayon GPS réel (voir _local_densities)
    df["densite_commune"], df["densite_segment"] = _local_densities(df)
    df["densite_yespark"] = _yespark_density(df).to_numpy()
    df["densite_transport"] = _transit_density(df).to_numpy()
    df = df.dropna(subset=["daily_rate", "age", "taux_occupation"])
    for c in NUM:                                    # PDP exige du float
        df[c] = df[c].astype(float)
    X = df[CAT + NUM].copy()
    y = df["taux_occupation"].to_numpy()
    return df, X, y


def _pipe(model):
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=8,
                              sparse_output=False), CAT),
        ("num", StandardScaler(), NUM),
    ])
    return Pipeline([("pre", pre), ("model", model)])


# ---------------------------------------------------------------------------
# 1-3. Évaluation croisée : naïfs vs Ridge vs Gradient Boosting
# ---------------------------------------------------------------------------
def evaluate(X, y, df):
    kf = KFold(n_splits=5, shuffle=True, random_state=0)
    seg = df["segment"].to_numpy()
    res = {k: {"mae": [], "r2": []} for k in
           ["naif_moyenne", "moyenne_segment", "ridge", "gradient_boosting"]}

    for tr, te in kf.split(X):
        # baselines
        res["naif_moyenne"]["mae"].append(mean_absolute_error(y[te], np.full(len(te), y[tr].mean())))
        res["naif_moyenne"]["r2"].append(r2_score(y[te], np.full(len(te), y[tr].mean())))
        seg_mean = pd.Series(y[tr]).groupby(seg[tr]).mean()
        pred_seg = pd.Series(seg[te]).map(seg_mean).fillna(y[tr].mean()).to_numpy()
        res["moyenne_segment"]["mae"].append(mean_absolute_error(y[te], pred_seg))
        res["moyenne_segment"]["r2"].append(r2_score(y[te], pred_seg))
        # modèles
        for name, mdl in [("ridge", Ridge(alpha=1.0)),
                          ("gradient_boosting",
                           HistGradientBoostingRegressor(max_depth=4, learning_rate=0.08,
                                                         max_iter=300, l2_regularization=1.0,
                                                         random_state=0))]:
            p = _pipe(mdl).fit(X.iloc[tr], y[tr])
            pr = np.clip(p.predict(X.iloc[te]), 0, 1)
            res[name]["mae"].append(mean_absolute_error(y[te], pr))
            res[name]["r2"].append(r2_score(y[te], pr))

    print("=== Performance (validation croisée 5 folds) ===")
    print(f"{'modèle':20} {'MAE':>8} {'R²':>8}")
    for k, v in res.items():
        print(f"{k:20} {np.mean(v['mae']):8.4f} {np.mean(v['r2']):8.3f}")
    return res


# ---------------------------------------------------------------------------
# 4. Interprétation : importance + dépendances partielles
# ---------------------------------------------------------------------------
def interpret(X, y):
    """Fit + importance + dépendances partielles. Renvoie (modèle, résultats)
    où `résultats` est un dict JSON-ready (réutilisé par webapp/data.py) —
    les mêmes calculs alimentent l'affichage console et l'API."""
    hgb = _pipe(HistGradientBoostingRegressor(max_depth=4, learning_rate=0.08,
                                             max_iter=300, l2_regularization=1.0,
                                             random_state=0)).fit(X, y)

    print("\n=== Importance des variables (permutation) ===")
    imp = permutation_importance(hgb, X, y, n_repeats=10, random_state=0,
                                 scoring="neg_mean_absolute_error")
    order = np.argsort(imp.importances_mean)[::-1]
    importance = [{"feature": (CAT + NUM)[i], "value": float(imp.importances_mean[i])}
                  for i in order]
    for row in importance:
        print(f"  {row['feature']:18} {row['value']:+.4f}")

    print("\n=== Dépendances partielles (effet moyen sur l'occupation) ===")
    partial_dep = {}
    for feat in ["daily_rate", "age", "densite_segment", "densite_yespark", "densite_transport"]:
        pd_res = partial_dependence(hgb, X, [feat], grid_resolution=6)
        xs = pd_res["grid_values"][0]
        ys = pd_res["average"][0]
        partial_dep[feat] = [{"x": float(x), "y": float(v)} for x, v in zip(xs, ys)]
        pts = " | ".join(f"{x:.0f}->{v*100:.1f}%" for x, v in zip(xs, ys))
        print(f"  {feat:16}: {pts}")

    print("\n=== Effet du prix par segment (occupation prédite) ===")
    base = X.median(numeric_only=True)
    price_effect = {}
    for seg in ["Citadine", "SUV", "Utilitaire"]:
        row = {**{c: X[c].mode()[0] for c in CAT}, **base.to_dict(), "segment": seg}
        line, points = [], []
        for prix in [35, 50, 65, 80, 100]:
            r = pd.DataFrame([{**row, "daily_rate": prix}])[CAT + NUM]
            occ = float(np.clip(hgb.predict(r), 0, 1)[0])
            points.append({"price": prix, "occupation": occ})
            line.append(f"{prix}€->{occ*100:.0f}%")
        price_effect[seg] = points
        print(f"  {seg:12}: " + "  ".join(line))

    results = {"importance": importance, "partial_dependence": partial_dep,
              "price_effect": price_effect}
    return hgb, results


def run_full(min_passages: int = 3) -> dict:
    """Enchaîne prepare/evaluate/interpret et renvoie un dict JSON-ready
    (perf CV + importance + dépendances partielles + effet prix) pour le
    dashboard web (webapp/data.py)."""
    df, X, y = prepare(min_passages=min_passages)
    perf = evaluate(X, y, df)
    _, insights = interpret(X, y)
    return {
        "n_vehicules": len(X),
        "occupation_moyenne": float(y.mean()),
        "performance": {k: {"mae": float(np.mean(v["mae"])), "r2": float(np.mean(v["r2"]))}
                        for k, v in perf.items()},
        **insights,
    }


def main():
    df, X, y = prepare()
    print(f"{len(X)} véhicules, occupation moyenne {y.mean()*100:.1f}%\n")
    evaluate(X, y, df)
    interpret(X, y)


if __name__ == "__main__":
    main()

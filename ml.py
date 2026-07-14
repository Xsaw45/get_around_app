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

Feature maison : `densite_segment` = nb de véhicules du même segment dans la même
commune → capture la SURREPRÉSENTATION (l'hypothèse Kangoo Express).

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

from features import load_snapshots
from pipeline import build_features

CAT = ["segment", "propulsion", "make"]
NUM = ["age", "daily_rate", "fiabilite_score", "densite_commune", "densite_segment"]


# ---------------------------------------------------------------------------
# Préparation des données
# ---------------------------------------------------------------------------
def prepare(min_passages: int = 3):
    feats = build_features(load_snapshots(), min_passages=min_passages)
    df = feats.copy()
    df["make"] = df["make"].fillna("?")
    # feature d'offre locale (concurrence) : nb de véhicules dans la commune
    df["densite_commune"] = df.groupby("commune")["uid"].transform("size")
    df["densite_segment"] = df.groupby(["commune", "segment"])["uid"].transform("size")
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
    hgb = _pipe(HistGradientBoostingRegressor(max_depth=4, learning_rate=0.08,
                                             max_iter=300, l2_regularization=1.0,
                                             random_state=0)).fit(X, y)

    print("\n=== Importance des variables (permutation) ===")
    imp = permutation_importance(hgb, X, y, n_repeats=10, random_state=0,
                                 scoring="neg_mean_absolute_error")
    order = np.argsort(imp.importances_mean)[::-1]
    for i in order:
        print(f"  {(CAT+NUM)[i]:18} {imp.importances_mean[i]:+.4f}")

    print("\n=== Dépendances partielles (effet moyen sur l'occupation) ===")
    for feat in ["daily_rate", "age", "densite_segment"]:
        pd_res = partial_dependence(hgb, X, [feat], grid_resolution=6)
        xs = pd_res["grid_values"][0]
        ys = pd_res["average"][0]
        pts = " | ".join(f"{x:.0f}->{v*100:.1f}%" for x, v in zip(xs, ys))
        print(f"  {feat:16}: {pts}")

    print("\n=== Effet du prix par segment (occupation prédite) ===")
    base = X.median(numeric_only=True)
    for seg in ["Citadine", "SUV", "Utilitaire"]:
        row = {**{c: X[c].mode()[0] for c in CAT}, **base.to_dict(), "segment": seg}
        line = []
        for prix in [35, 50, 65, 80, 100]:
            r = pd.DataFrame([{**row, "daily_rate": prix}])[CAT + NUM]
            line.append(f"{prix}€->{float(np.clip(hgb.predict(r),0,1))*100:.0f}%")
        print(f"  {seg:12}: " + "  ".join(line))
    return hgb


def main():
    df, X, y = prepare()
    print(f"{len(X)} véhicules, occupation moyenne {y.mean()*100:.1f}%\n")
    evaluate(X, y, df)
    interpret(X, y)


if __name__ == "__main__":
    main()

"""
tco.py — Coût total de possession & optimisation buy/sell (cœur de l'assistant)
==============================================================================

Assemble toutes les briques en UNE équation de rentabilité nette dans le temps :

    net(âge) = revenus_locatifs(âge) − entretien(âge) − décote(âge) − frais_fixes

- revenus  : occupation (mesurée) × prix/jour × 365 × part_proprietaire
- entretien: reliability.maintenance_cost(motorisation, segment, âge)
- décote   : perte de valeur sur l'année = valeur(âge) − valeur(âge+1)
- frais    : assurance, CT, divers (paramètre)

De là on répond aux 2 questions de ton assistant :
  • QUAND VENDRE  = fin de la fenêtre où net(âge) reste positif (avant que
    entretien + décote ne dépassent les revenus).
  • À QUEL ÂGE / KM ACHETER = début de cette fenêtre (après la grosse décote
    initiale, avant le mur des pannes). Le km se lit via reliability.km_estime.

⚠️ Décote = courbe paramétrique par segment (pas d'API Argus gratuite), éditable.
⚠️ part_proprietaire : le prix GBFS est ce que paie le LOCATAIRE ; Getaround
   prend une commission -> mets ta vraie part (def. 0.70 en ordre de grandeur).
⚠️ Tant que l'occupation est préliminaire, les montants sont illustratifs : c'est
   l'OUTIL et la forme des courbes qui comptent.
"""
from __future__ import annotations
import pandas as pd

import reliability
from reliability import _interp
from pipeline import _new_price, segment_of, build_features


# ---------------------------------------------------------------------------
# Décote : valeur résiduelle (% du prix neuf) par âge, ajustée par segment
# ---------------------------------------------------------------------------
_RESID_ANCHORS = {0: 1.00, 1: 0.78, 2: 0.67, 3: 0.59, 4: 0.52, 5: 0.46,
                  6: 0.41, 7: 0.36, 8: 0.32, 10: 0.25, 12: 0.20, 15: 0.14}
# >1 = tient mieux sa valeur (utilitaires, SUV) ; <1 = décote plus vite
_DEPREC_SEG = {"Utilitaire": 1.15, "Minibus": 1.12, "SUV": 1.03, "Familiale": 1.02,
               "Citadine": 1.00, "Berline": 0.93, "Cabriolet": 0.95, "Autre": 1.0}


def residual_value(new_price: float, age: float, segment: str) -> float:
    frac = _interp(_RESID_ANCHORS, max(0.0, age)) * _DEPREC_SEG.get(segment, 1.0)
    return round(new_price * min(1.0, max(0.06, frac)), 0)


# ---------------------------------------------------------------------------
# Table de détention année par année
# ---------------------------------------------------------------------------
def holding_table(segment: str, propulsion: str, occupancy: float,
                  daily_rate: float, make: str = "", model: str = "",
                  owner_share: float = 0.70, fixed_annual: float = 700.0,
                  max_age: int = 15) -> pd.DataFrame:
    """Économie de possession par âge (0..max_age) pour un profil de véhicule."""
    new = _new_price(make, model, segment)
    rows = []
    for a in range(0, max_age + 1):
        val = residual_value(new, a, segment)
        val_next = residual_value(new, a + 1, segment)
        deprec = val - val_next
        revenue = occupancy * daily_rate * 365.0 * owner_share
        maint = reliability.maintenance_cost(propulsion, segment, a)
        net = revenue - maint - deprec - fixed_annual
        rows.append(dict(age=a, km=reliability.reliability_of(make, model, a)["km_estime"],
                         valeur=val, revenus=round(revenue), entretien=round(maint),
                         decote=round(deprec), frais=round(fixed_annual),
                         net_annuel=round(net),
                         defaut_pct=reliability.reliability_of(make, model, a)["defaut_pct"]))
    return pd.DataFrame(rows)


def recommend(segment, propulsion, occupancy, daily_rate, make="", model="",
              **kw) -> dict:
    """Fenêtre de détention rentable -> âge/km d'achat et de revente conseillés."""
    t = holding_table(segment, propulsion, occupancy, daily_rate, make, model, **kw)
    pos = t[t["net_annuel"] > 0]
    if pos.empty:
        return dict(table=t, rentable=False,
                    message="Aucune année à net positif avec ces hypothèses.")
    buy, sell = int(pos["age"].min()), int(pos["age"].max())
    return dict(
        table=t, rentable=True,
        acheter_age=buy, acheter_km=int(t.loc[t["age"] == buy, "km"].iloc[0]),
        vendre_age=sell, vendre_km=int(t.loc[t["age"] == sell, "km"].iloc[0]),
        profit_total=int(pos["net_annuel"].sum()),
        message=(f"Acheter vers {buy} ans (~{t.loc[t['age']==buy,'km'].iloc[0]//1000}k km), "
                 f"revendre vers {sell} ans (~{t.loc[t['age']==sell,'km'].iloc[0]//1000}k km)."))


def breakeven_occupancy(segment, propulsion, daily_rate, make="", model="",
                        buy_age: int = 4, hold: int = 5,
                        owner_share: float = 0.70, fixed_annual: float = 700.0) -> float:
    """Occupation MINIMALE pour être à l'équilibre si on détient le véhicule de
    buy_age à buy_age+hold. Revenu étant linéaire en occupation, calcul direct."""
    new = _new_price(make, model, segment)
    deprec = residual_value(new, buy_age, segment) - residual_value(new, buy_age + hold, segment)
    couts = deprec + hold * fixed_annual + sum(
        reliability.maintenance_cost(propulsion, segment, buy_age + i) for i in range(hold))
    revenu_par_point = hold * daily_rate * 365.0 * owner_share   # pour occ=1.0
    return round(couts / revenu_par_point, 3)


# ---------------------------------------------------------------------------
# DÉMO sur des modèles réels (occupation/prix tirés de tes données)
# ---------------------------------------------------------------------------
def _demo():
    feats = build_features()
    prof = (feats.assign(mm=feats["make"].fillna("?") + " " + feats["model"].fillna("?"))
                 .groupby(["mm", "segment", "propulsion"])
                 .agg(occ=("taux_occupation", "mean"),
                      prix=("daily_rate", "mean"),
                      n=("uid", "nunique")).reset_index())
    prof = prof[prof["n"] >= 8].sort_values("n", ascending=False)

    print("=== SEUIL DE RENTABILITÉ : occupation requise vs observée ===")
    print("(détention 4->9 ans, part proprio 70%. 'requis'>'observé' = pas rentable)\n")
    for _, r in prof.head(12).iterrows():
        mk, md = r["mm"].split(" ", 1) if " " in r["mm"] else (r["mm"], "")
        be = breakeven_occupancy(r["segment"], r["propulsion"], r["prix"], mk, md)
        flag = "OK" if r["occ"] >= be else "-- "
        print(f"  {flag} {r['mm']:26} observé {r['occ']*100:4.1f}%  |  requis {be*100:4.1f}%")

    print("\n=== Détail détention : Toyota Yaris Hybride ===")
    row = prof[prof["mm"].str.contains("Yaris Hybride", case=False)].head(1)
    if not row.empty:
        r = row.iloc[0]
        t = holding_table(r["segment"], r["propulsion"], r["occ"], r["prix"],
                          make="Toyota", model="Yaris Hybride")
        print(t[["age", "km", "valeur", "revenus", "entretien", "decote",
                 "net_annuel", "defaut_pct"]].to_string(index=False))


if __name__ == "__main__":
    _demo()

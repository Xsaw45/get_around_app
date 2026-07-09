"""
reliability.py — Fiabilité & coûts d'entretien (nouvelle source pour le TCO)
==========================================================================

Objectif : ajouter aux véhicules deux dimensions absentes du GBFS —
  1. FIABILITÉ : taux de défaillance estimé selon modèle × âge (base TÜV),
  2. ENTRETIEN : coût annuel estimé selon motorisation × segment × âge.

Ça sert à deux choses :
  - une FEATURE de plus pour le modèle d'occupation (la fiabilité explique la
    surreprésentation de certains modèles et une part du prix) ;
  - la brique « coûts » du calcul de rentabilité NETTE et du buy/sell
    (revenus − décote − entretien/pannes).

⚠️ VALEURS DE RÉFÉRENCE, PAS DES COTES OFFICIELLES.
  - Le taux de défaillance = courbe d'âge moyenne (patterns TÜV Report) ×
    multiplicateur de fiabilité du modèle (réputation TÜV/ADAC). À VALIDER
    contre le TÜV Report réel / des données terrain. Facile à éditer.
  - Les coûts d'entretien viennent d'études françaises 2025-2026 (moyennes par
    motorisation). Sources : voir README / conversation.
  - Le GBFS ne donne PAS le kilométrage : on utilise le km MOYEN par âge (TÜV)
    comme proxy. Le raisonnement est au niveau modèle×âge, pas voiture par voiture.
"""
from __future__ import annotations
import pandas as pd


# ---------------------------------------------------------------------------
# 1. FIABILITÉ
# ---------------------------------------------------------------------------
# Taux moyen de défaillance grave au contrôle technique par âge (patterns TÜV).
# Interpolé linéairement entre ces points d'ancrage (âge -> % de défauts graves).
_AGE_DEFECT_ANCHORS = {2: 0.05, 3: 0.07, 5: 0.11, 7: 0.18,
                       9: 0.24, 11: 0.30, 13: 0.37, 15: 0.44}

# km moyen cumulé par âge (TÜV / marché FR ; ~14 000 km/an, plus pour diesel/vans)
_AGE_MILEAGE_ANCHORS = {2: 28000, 3: 42000, 5: 68000, 7: 96000,
                        9: 124000, 11: 150000, 13: 175000, 15: 198000}

# Multiplicateur de fiabilité par modèle (mot-clé -> facteur ; <1 = plus fiable).
# 1re correspondance gagne. Basé sur la réputation TÜV/ADAC — à affiner.
_MODEL_TIER: list[tuple[tuple[str, ...], float]] = [
    # très fiables
    (("yaris", "corolla", "c-hr", "proace",            # Toyota (dont util. rebadgé)
      "golf", "polo", "transporter", "t-cross", "t-roc",  # VW
      "mazda", "civic", "jazz"), 0.65),
    # fiables
    (("208", "2008", "308", "3008", "508",             # Peugeot récents
      "clio", "captur", "megane", "mégane",            # Renault récents
      "a1", "a3", "q3", "ibiza", "leon", "arona", "fabia", "octavia"), 0.82),
    # dans la moyenne
    (("c3", "c4", "berlingo", "ds 3", "ds3",           # Citroën
      "corsa", "astra", "crossland", "mokka",          # Opel
      "fiesta", "focus", "500", "panda", "micra", "note",
      "kangoo", "trafic", "expert", "jumpy", "scudo"), 1.0),  # utilitaires PSA/Renault
    # sous la moyenne
    (("twingo", "modus", "master", "boxer", "jumper",  # gros fourgons + vieux petits
      "ducato", "doblo", "fiorino", "tipo", "punto",   # Fiat
      "sandero", "logan", "duster", "dokker", "lodgy", "jogger", "spring",  # Dacia
      "movano", "nv200", "nv400", "vivaro"), 1.25),
]


def _interp(anchors: dict, x: float) -> float:
    ks = sorted(anchors)
    if x <= ks[0]:
        return anchors[ks[0]]
    if x >= ks[-1]:
        return anchors[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= x <= b:
            t = (x - a) / (b - a)
            return anchors[a] + t * (anchors[b] - anchors[a])
    return anchors[ks[-1]]


def _tier(make, model) -> float:
    m = f"{make or ''} {model or ''}".lower()
    for keys, factor in _MODEL_TIER:
        if any(k in m for k in keys):
            return factor
    return 1.0                                          # inconnu -> moyenne


def reliability_of(make, model, age: float) -> dict:
    """Retourne pour un (modèle, âge) : taux de défaillance estimé, km moyen,
    et un score de fiabilité 0-100 (100 = très fiable)."""
    if age is None or pd.isna(age):
        age = 8
    age = max(0, float(age))
    defect = min(0.85, _interp(_AGE_DEFECT_ANCHORS, age) * _tier(make, model))
    km = _interp(_AGE_MILEAGE_ANCHORS, age)
    score = round(100 * (1 - defect), 1)                # inverse, lisible
    return dict(defaut_pct=round(defect, 3), km_estime=int(km),
                fiabilite_score=score)


# ---------------------------------------------------------------------------
# 2. COÛT D'ENTRETIEN  (études FR 2025-2026)
# ---------------------------------------------------------------------------
# Coût annuel de base par motorisation (€, ~15 000 km/an), milieu des fourchettes.
_MAINT_BASE = {
    "combustion": 1150,          # essence 800-1500
    "combustion_diesel": 1300,   # diesel 900-1700 (FAP, huiles Low SAPS)
    "hybrid": 520,               # hybride 400-600 (freinage régénératif)
    "electric": 420,             # élec 300-600 (ni vidange ni courroie)
}
# Facteur par segment (grosses pièces / usure).
_MAINT_SEG = {
    "Citadine": 0.80, "Berline": 1.25, "SUV": 1.15, "Utilitaire": 1.20,
    "Minibus": 1.30, "Familiale": 1.00, "Cabriolet": 1.25, "Autre": 1.0,
}


def maintenance_cost(propulsion, segment, age: float) -> float:
    """Coût d'entretien+réparation annuel estimé (€). Monte avec l'âge
    (+6 %/an : pièces qui lâchent, hors garantie)."""
    if age is None or pd.isna(age):
        age = 8
    base = _MAINT_BASE.get(propulsion, 1150)
    seg = _MAINT_SEG.get(segment, 1.0)
    return round(base * seg * (1 + 0.06 * max(0, float(age))), 0)


# ---------------------------------------------------------------------------
# 3. Enrichissement d'une table de features (pipeline)
# ---------------------------------------------------------------------------
def enrich(feats: pd.DataFrame, ref_year: int) -> pd.DataFrame:
    """Ajoute fiabilité + entretien + rentabilité NETTE à une table build_features."""
    df = feats.copy()
    age = ref_year - pd.to_numeric(df["year"], errors="coerce")
    rel = [reliability_of(mk, md, a)
           for mk, md, a in zip(df["make"], df["model"], age)]
    df["age"] = age
    df["defaut_pct"] = [r["defaut_pct"] for r in rel]
    df["fiabilite_score"] = [r["fiabilite_score"] for r in rel]
    df["km_estime"] = [r["km_estime"] for r in rel]
    df["entretien_annuel"] = [maintenance_cost(p, s, a) for p, s, a
                              in zip(df["propulsion"], df["segment"], age)]
    # rentabilité NETTE : revenus − entretien (la décote viendra dans le TCO)
    df["revenu_net_annuel"] = df["revenu_annuel"] - df["entretien_annuel"]
    df["roi_net"] = df["revenu_net_annuel"] / df["cout_acquisition"]
    return df


def _preview():
    print("=== FIABILITÉ par modèle × âge (extrait) ===")
    for mk, md in [("Toyota", "Yaris"), ("Renault", "Clio"),
                   ("Renault", "Trafic"), ("Fiat", "Ducato"),
                   ("Dacia", "Sandero"), ("Peugeot", "208")]:
        for age in (3, 8, 12):
            r = reliability_of(mk, md, age)
            print(f"  {mk} {md} {age}a -> défauts {r['defaut_pct']*100:4.1f}% | "
                  f"score {r['fiabilite_score']:5.1f} | ~{r['km_estime']//1000} k km")
    print("\n=== ENTRETIEN annuel (€) par motorisation × segment × âge ===")
    for prop in ("combustion", "combustion_diesel", "hybrid", "electric"):
        row = " | ".join(f"{seg[:4]} {maintenance_cost(prop, seg, 8):.0f}"
                         for seg in ("Citadine", "Utilitaire", "SUV"))
        print(f"  {prop:18} (8 ans): {row}")


if __name__ == "__main__":
    _preview()

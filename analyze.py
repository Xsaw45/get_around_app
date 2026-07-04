"""
analyze.py — Rapport d'étude de marché depuis la base de snapshots
==================================================================

Produit un livrable descriptif immédiat (dès le 1er passage) :
  exports/market_report.md   — synthèse chiffrée
  exports/market_overview.png — 4 graphiques (communes, prix, marques, énergie)

Quand plusieurs passages seront accumulés, ajoute l'occupation et le pricing
dynamique via features.py (voir la fin du rapport).
"""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")                     # pas d'affichage, on écrit un PNG
import matplotlib.pyplot as plt

from config import EXPORT_DIR
from features import (load_snapshots, collection_grid, market_snapshot,
                      utilization_per_vehicle, price_dynamics)


def _fig(last):
    fig, ax = plt.subplots(2, 2, figsize=(13, 9))
    fig.suptitle("Getaround IDF — photo de marché (flotte libre-service)",
                 fontsize=14, weight="bold")

    # communes
    last["commune"].value_counts().head(12).iloc[::-1].plot.barh(
        ax=ax[0, 0], color="#3b7dd8")
    ax[0, 0].set_title("Véhicules par commune (top 12)")
    ax[0, 0].set_xlabel("nb véhicules")

    # prix
    ax[0, 1].hist(last["daily_rate"].dropna(), bins=30, color="#e0803b")
    ax[0, 1].set_title("Distribution du prix / jour (€)")
    ax[0, 1].set_xlabel("€/jour")
    ax[0, 1].axvline(last["daily_rate"].median(), color="k", ls="--", lw=1,
                     label=f"médiane {last['daily_rate'].median():.0f} €")
    ax[0, 1].legend()

    # marques
    last["make"].value_counts().head(10).iloc[::-1].plot.barh(
        ax=ax[1, 0], color="#4caf82")
    ax[1, 0].set_title("Véhicules par marque (top 10)")
    ax[1, 0].set_xlabel("nb véhicules")

    # motorisation
    last["propulsion"].value_counts().plot.pie(
        ax=ax[1, 1], autopct="%1.0f%%", ylabel="")
    ax[1, 1].set_title("Motorisation")

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


def build():
    EXPORT_DIR.mkdir(exist_ok=True)
    snap = load_snapshots()
    if snap.empty:
        print("Base vide — lance d'abord ingest.py.")
        return
    ms = market_snapshot(snap)
    last = snap[snap["snapshot_ts"] == snap["snapshot_ts"].max()]
    n_ts = snap["snapshot_ts"].nunique()

    fig = _fig(last)
    png = EXPORT_DIR / "market_overview.png"
    fig.savefig(png, dpi=110)
    plt.close(fig)

    lines = [
        "# Getaround IDF — rapport d'étude de marché",
        "",
        f"- **Passage analysé** : {ms['horodatage']}",
        f"- **Passages en base** : {n_ts}",
        f"- **Flotte** : {ms['n_vehicules']} véhicules sur "
        f"{ms['n_communes']} communes",
        f"- **Prix/jour** : médiane {last['daily_rate'].median():.0f} € "
        f"(min {last['daily_rate'].min():.0f} / "
        f"max {last['daily_rate'].max():.0f})",
        "",
        "## Répartition géographique (top 10 communes)",
        "",
        "| Commune | Véhicules |",
        "|---|---|",
    ]
    for commune, n in ms["par_commune"].head(10).items():
        lines.append(f"| {commune} | {n} |")

    lines += ["", "## Parc par marque (top 8)", "",
              "| Marque | Véhicules |", "|---|---|"]
    for make, n in ms["par_marque"].head(8).items():
        lines.append(f"| {make} | {n} |")

    lines += ["", "## Motorisation", "", "| Type | Véhicules |", "|---|---|"]
    for prop, n in ms["par_motorisation"].items():
        lines.append(f"| {prop} | {n} |")

    if n_ts >= 2:
        grid = collection_grid()
        util = utilization_per_vehicle(snap, grid=grid)
        pdyn = price_dynamics(snap)
        lines += [
            "", "## Demande mesurée",
            f"- Occupation moyenne : {util['taux_occupation'].mean():.1%}",
            f"- Véhicules avec ≥1 location détectée : "
            f"{(util['n_episodes'] > 0).sum()} / {len(util)}",
            f"- Véhicules à prix variable (pricing dynamique) : "
            f"{int(pdyn['prix_variable'].sum())}",
        ]
    else:
        lines += ["", "> Un seul passage : occupation et pricing dynamique "
                  "s'activent dès le 2ᵉ. Laisse la collecte tourner."]

    md = EXPORT_DIR / "market_report.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Rapport écrit : {md}")
    print(f"Graphiques  : {png}")


if __name__ == "__main__":
    build()

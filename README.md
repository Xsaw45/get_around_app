# Getaround IDF — série temporelle depuis l'open data GBFS

Collecte régulière de la **flotte Getaround en libre-service** (Île-de-France)
pour constituer une base temporelle et faire de l'**étude de marché** + du **ML**
(prédiction de demande, pricing dynamique, scoring de rentabilité).

## Pourquoi GBFS et pas du scraping

Getaround publie sa flotte libre-service au format ouvert **GBFS v3** sur
data.gouv.fr (obligation légale LOM). On lit ce JSON officiel — pas de scraping
HTML, pas de sélecteurs fragiles, pas de zone grise CGU/robots.txt, fiabilité
**99,9 %**. Chaque véhicule vient avec sa **position GPS**, son **prix exact**,
son **modèle/année/motorisation** et un **id d'annonce stable**.

Source : `https://fr.getaround.com/gbfs/manifest?country_code=FR`
→ système `getaround_paris` (Paris + petite couronne, 69 communes).

## L'astuce temporelle

Getaround **retire du flux les voitures actuellement louées**. Donc :

> une **location** = un véhicule **présent** à un passage, **absent** au suivant,
> puis qui **réapparaît** (le retour).

En photographiant le flux toutes les ~20 min et en reconstituant la présence de
chaque véhicule, on mesure la **demande réelle** — ce que ni le nombre d'avis ni
une photo unique ne donnent :

| Fonction (`features.py`) | Signal |
|---|---|
| `rental_episodes()` | locations détectées (disparition→réapparition) + durée |
| `utilization_per_vehicle()` | taux d'occupation réel par véhicule |
| `price_dynamics()` | variations de prix dans le temps (pricing dynamique) |
| `market_snapshot()` | photo descriptive : flotte, communes, prix, motorisation |
| `rentability_by_group()` | revenu annualisé & ratio rentabilité par modèle |

## Installation

```powershell
pip install -r requirements.txt
```

> La **collecte** (`ingest.py`) n'a **aucune dépendance** (stdlib seule).
> pandas/pyarrow ne servent que pour l'analyse et les exports.

## Mise en route

```powershell
python ingest.py        # un passage : ~1000 véhicules -> getaround_gbfs.sqlite
python features.py      # analyse console (descriptif dès 1 passage ; demande dès 2+)
python analyze.py       # rapport d'étude de marché (md + graphiques) dans ./exports/
python export_ml.py     # datasets Parquet/CSV pour le ML dans ./exports/
python tests/test_features.py   # vérifie le moteur temporel (6 tests)
```

## Planification (le cœur du projet)

La valeur vient de la **régularité**. Comme les voitures louées disparaissent du
flux, il faut collecter **toutes les ~20 min** (un rythme quotidien raterait
toutes les locations de quelques heures). L'agrégation au **jour** se fait à
l'analyse, pas à la collecte.

Sur Windows, une fois :

```powershell
.\register_task.ps1     # enregistre une tâche planifiée toutes les 20 min
```

La tâche appelle `run.ps1` (force l'UTF-8, journalise dans `run.log`). Le PC doit
être allumé aux heures de passage ; pour une série sans trou, héberger sur une
petite VM/serveur qui tourne 24/7 (le code y est identique).

## Schéma de données (`getaround_gbfs.sqlite`, append-only)

- `vehicle_snapshots` — 1 ligne / véhicule / passage : position, dispo, prix,
  modèle, `listing_id` (clé stable), commune.
- `vehicle_types` — référentiel modèle/année/motorisation.
- `pricing_plans` — tarifs par plan, historisés à chaque passage.
- `run_log` — trace de chaque passage.

## Étendre le périmètre

Grande couronne (Meaux, Val d'Europe, 77…) : ajouter les slugs de villes dans
`SYSTEMS` (`config.py`). Les doublons entre systèmes sont dédupliqués par
`listing_id`, et le filtre `IDF_BBOX` écarte le hors-zone.

## Limites honnêtes

- Flux Paris **plafonné à 1000 véhicules** : une voiture peut disparaître parce
  qu'elle sort du top-1000, pas seulement parce qu'elle est louée. Filtre :
  `min_absence_snapshots` dans `rental_episodes()`.
- C'est la flotte **libre-service**, pas tout le marché P2P entre particuliers,
  et **sans avis/notes** (absents du GBFS).
- Pas de coût d'acquisition dans la source : on a marque/modèle/année pour
  l'estimer (Argus) et alimenter `rentability_by_group(acquisition_cost=…)`.

## Fichiers

- `config.py` — périmètre, cadence, chemins
- `gbfs.py` — client GBFS (lecture JSON, normalisation)
- `ingest.py` — un passage de collecte (append-only)
- `features.py` — moteur temporel (présence → locations, occupation, prix)
- `analyze.py` — rapport d'étude de marché (markdown + graphiques PNG)
- `export_ml.py` — datasets Parquet/CSV pour le ML
- `tests/test_features.py` — tests du moteur temporel (python pur ou pytest)
- `run.ps1` / `register_task.ps1` — planification Windows
- `legacy/` — ancien scraper HTML (abandonné au profit du GBFS)

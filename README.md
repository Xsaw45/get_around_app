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
flux, il faut collecter souvent (un rythme quotidien raterait les locations de
quelques heures). L'agrégation au **jour** se fait à l'analyse, pas à la collecte.

### Option A — GitHub Actions (recommandé, vraiment automatique)

Le collecteur tourne sur les serveurs GitHub, **PC éteint ou non** :
`.github/workflows/collect.yml` s'exécute toutes les 30 min, lance `ingest.py`
et **committe les données** dans le repo (le runner est jetable ⇒ seul le commit
persiste). D'où le stockage en CSV (`data/`) plutôt qu'en SQLite : un binaire qui
gonfle serait vite refusé par la limite de 100 Mo/fichier de GitHub.

Pour l'activer : pousse le repo sur GitHub, va dans l'onglet **Actions**,
active les workflows. C'est tout — il tourne ensuite tout seul. Bouton
**Run workflow** pour un test immédiat.

Notes :
- Repo **public** → minutes Actions **gratuites illimitées**. Repo **privé** →
  ~1440 min/mois à cette cadence (dans le quota gratuit de 2000).
- Le planificateur GitHub est *best effort* : un passage peut être décalé de
  quelques minutes en forte charge. Pour une cadence stricte, voir l'option B.

### Option B — VM cloud 24/7 gratuite (cadence stricte, sans jitter)

VM **gratuite à vie** (Google Cloud e2-micro ou Oracle Always Free) qui collecte
toutes les 20 min et pousse dans le repo. Guide pas-à-pas + scripts prêts :
**[`deploy/README.md`](deploy/README.md)**. En résumé, sur la VM :

```bash
curl -fsSL https://raw.githubusercontent.com/Xsaw45/get_around_app/main/deploy/vm_setup.sh | bash
```

> N'utilise qu'un seul planificateur : si tu passes à la VM, désactive le
> workflow Actions (sinon les deux poussent en même temps).

### Option C — PC Windows (Planificateur de tâches)

Simple mais **ne collecte que quand le PC est allumé** (trous sinon) :

```powershell
.\register_task.ps1     # tâche planifiée toutes les 20 min -> run.ps1
```

## Voir que ça marche / visualiser

- **Le rapport visuel** : ouvre le dossier **[`reports/`](reports/)** sur GitHub —
  le `README.md` s'y affiche tout seul avec les graphiques (communes, prix,
  marques, motorisation) et un **voyant de fraîcheur** (🟢 à jour / 🔴 arrêté).
  Régénéré automatiquement toutes les 3 h par `.github/workflows/report.yml`.
- **La collecte tourne ?** Onglet **Actions** du repo : des exécutions vertes
  « Collecte GBFS Getaround », et des commits `data: passage …` qui apparaissent.
- **En local** : `python analyze.py` régénère le rapport dans `reports/`.

## Stockage des données (append-only)

**Source canonique = `data/` (CSV, committé dans git)** — portable et compatible
avec les runners jetables de GitHub Actions :

- `data/AAAA-MM-JJ.csv` — 1 ligne / véhicule / passage : position, dispo, prix,
  modèle, `listing_id` (clé stable), commune. Un fichier par jour (borné).
- `data/runs.csv` — trace de chaque passage (grille temporelle autoritative).

**Miroir local optionnel = `getaround_gbfs.sqlite`** (non committé) — pratique
pour du SQL ad hoc ; contient en plus `vehicle_types` et `pricing_plans`.
`features.py` lit `data/` en priorité, et retombe sur le SQLite sinon.

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
- `.github/workflows/collect.yml` — collecte automatique (GitHub Actions)
- `run.ps1` / `register_task.ps1` — planification Windows (option C)
- `data/` — données collectées (CSV, committé)
- `legacy/` — ancien scraper HTML (abandonné au profit du GBFS)

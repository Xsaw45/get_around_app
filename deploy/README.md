# Déployer la collecte sur une VM gratuite 24/7

Alternative à GitHub Actions, pour une cadence stricte (20 min, sans jitter).
La VM tourne en permanence, collecte, et pousse les données dans le repo.

> **N'utilise qu'UN seul planificateur** : si tu passes à la VM, désactive le
> workflow GitHub Actions (repo → Actions → "Collecte GBFS Getaround" → Disable),
> sinon les deux poussent en même temps.

## Option recommandée : Google Cloud e2-micro (gratuit à vie)

1. Va sur <https://console.cloud.google.com> → crée un compte (carte bancaire
   demandée pour vérification, **non débitée** dans le palier gratuit).
2. **Compute Engine → Create instance** :
   - Region : **us-west1**, **us-central1** ou **us-east1** (obligatoire pour le
     gratuit) ;
   - Machine type : **e2-micro** ;
   - Boot disk : Ubuntu 22.04 LTS, disque standard ≤ 30 Go ;
   - crée l'instance.
3. Clique **SSH** à côté de l'instance (terminal dans le navigateur).
4. Colle cette commande — elle installe tout et programme cron :
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Xsaw45/get_around_app/main/deploy/vm_setup.sh | bash
   ```
5. Le script affiche une **clé publique**. GitHub → repo → **Settings → Deploy
   keys → Add deploy key** → colle-la → **coche "Allow write access"** → Add.
6. Teste un passage : `~/get_around_app/deploy/collect.sh`
   (doit créer un commit `data: passage …`).

C'est fini : cron collecte toutes les 20 min. Logs dans `~/get_around_app/cron.log`.

## Alternative : Oracle Cloud Always Free

Plus de ressources (VM ARM 2 OCPU / 12 Go), mais inscription plus capricieuse
(carte parfois refusée, capacité ARM parfois indisponible → prendre alors une
**VM.Standard.E2.1.Micro** AMD, toujours gratuite). Une fois la VM Ubuntu créée
et accessible en SSH, les étapes 4→6 ci-dessus sont **identiques**.

## Ce que fait la VM

- `deploy/vm_setup.sh` — installe git+python, clone le repo, génère une clé SSH
  de déploiement, programme cron (*/20).
- `deploy/collect.sh` — un passage : `git pull` → `python3 ingest.py` →
  commit + push des nouveaux CSV de `data/`.

# Cadence fiable 20 min via cron-job.org (gratuit, sans carte)

Le planificateur `schedule` de GitHub Actions est bridé (passages sautés, trous
de plusieurs heures). Le déclencheur `workflow_dispatch`, lui, démarre tout de
suite. On utilise donc un cron externe gratuit pour appuyer sur ce bouton toutes
les 20 min via l'API GitHub. **Aucun changement de code.**

## 1. Créer un token GitHub (fine-grained)

1. GitHub → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. **Resource owner** : Xsaw45.
3. **Repository access** : *Only select repositories* → **get_around_app**.
4. **Permissions → Repository permissions → Actions** : **Read and write**.
   ⚠️ Bien choisir **« Actions »** (décrite *« Workflows, workflow runs and
   artifacts »*), **PAS** la permission **« Workflows »** (elle, sert à modifier
   les fichiers de workflow, ce n'est pas ce qu'on veut). Au final le token doit
   montrer : **Metadata (Read-only) + Actions (Read and write)**.
5. Génère, **copie le token** (`github_pat_…`) — il ne se réaffiche plus.

## 2. Créer le cron sur cron-job.org

Crée un compte sur <https://cron-job.org> (e-mail, pas de carte), puis
**Create cronjob** avec :

- **URL** :
  ```
  https://api.github.com/repos/Xsaw45/get_around_app/actions/workflows/collect.yml/dispatches
  ```
- **Schedule** : toutes les 20 minutes (*Every 20 minutes*).
- Section **Advanced / Request** :
  - **Request method** : `POST`
  - **Headers** :
    ```
    Accept: application/vnd.github+json
    Authorization: Bearer github_pat_TON_TOKEN_ICI
    X-GitHub-Api-Version: 2022-11-28
    Content-Type: application/json
    ```
  - **Request body** :
    ```json
    {"ref":"main"}
    ```
- Enregistre.

## 3. Vérifier

- cron-job.org montre le résultat de chaque appel : un **204** = succès.
- Onglet **Actions** du repo : un run « Collecte GBFS Getaround » déclenché
  toutes les 20 min (badge *manually triggered* / via API, pas *Scheduled*).
- Les commits `data: passage …` s'espacent maintenant de ~20 min.

## Notes

- Le token ne vit que sur cron-job.org ; fine-grained + limité à ce repo + à
  Actions, le risque est minimal. Tu peux le révoquer à tout moment (Settings →
  Developer settings).
- On peut alors **supprimer le bloc `schedule:`** de `collect.yml` (le cron
  externe le remplace), ou le garder en filet de sécurité — il ne gêne pas.
- Le workflow `report.yml` peut rester sur son `schedule` (3 h) : un léger retard
  sur le rapport n'a aucune importance.

#!/usr/bin/env bash
# collect.sh — un passage de collecte sur la VM, appelé par cron toutes les 20 min.
# Met à jour le repo, collecte, committe et pousse les données vers GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."

# se resynchroniser d'abord (au cas où d'autres commits existent)
git pull --rebase --autostash origin main >/dev/null 2>&1 || true

python3 ingest.py

git add data/
if ! git diff --cached --quiet; then
    git commit -q -m "data: passage $(date -u +%Y-%m-%dT%H:%MZ)"
    git push -q origin main
fi

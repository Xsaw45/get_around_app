#!/usr/bin/env bash
# vm_setup.sh — installe le collecteur sur une VM Linux (Ubuntu/Debian) et
# programme cron toutes les 20 min. À lancer UNE fois sur la VM :
#
#   curl -fsSL https://raw.githubusercontent.com/Xsaw45/get_around_app/main/deploy/vm_setup.sh | bash
#
# À la fin, il affiche une CLÉ PUBLIQUE à coller dans GitHub (Deploy key, avec
# accès écriture) pour que la VM puisse pousser les données. Idempotent : on peut
# le relancer sans casse.
set -euo pipefail

REPO_HTTPS="https://github.com/Xsaw45/get_around_app.git"
REPO_SSH="git@github.com:Xsaw45/get_around_app.git"
REPO_DIR="$HOME/get_around_app"

echo ">> Installation des paquets (git, python3)..."
sudo apt-get update -y -qq
sudo apt-get install -y -qq git python3

echo ">> Récupération du repo..."
if [ ! -d "$REPO_DIR/.git" ]; then
    git clone -q "$REPO_HTTPS" "$REPO_DIR"
fi
cd "$REPO_DIR"
git remote set-url origin "$REPO_SSH"
git config user.name  "getaround-vm"
git config user.email "getaround-vm@local"
chmod +x deploy/*.sh

echo ">> Clé SSH de déploiement..."
mkdir -p "$HOME/.ssh"
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -C "getaround-vm" -q
fi
ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
sort -u -o "$HOME/.ssh/known_hosts" "$HOME/.ssh/known_hosts"

echo ">> Programmation cron (toutes les 20 min)..."
CRON_LINE="*/20 * * * * $REPO_DIR/deploy/collect.sh >> $REPO_DIR/cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'deploy/collect.sh' ; echo "$CRON_LINE" ) | crontab -

cat <<EOF

==================================================================
 SETUP TERMINÉ. Dernière étape (1 min) : autoriser la VM à pousser.

 1) Copie la clé publique ci-dessous :
------------------------------------------------------------------
$(cat "$HOME/.ssh/id_ed25519.pub")
------------------------------------------------------------------
 2) GitHub -> repo get_around_app -> Settings -> Deploy keys
    -> Add deploy key -> colle la clé -> COCHE "Allow write access" -> Add.

 3) Teste tout de suite un passage :
       $REPO_DIR/deploy/collect.sh
    (doit finir sans erreur et créer un commit "data: passage ...")

 Ensuite cron collecte tout seul toutes les 20 min. Logs : $REPO_DIR/cron.log
 IMPORTANT : désactive le workflow GitHub Actions pour éviter le double
 (repo -> Actions -> "Collecte GBFS Getaround" -> ... -> Disable workflow).
==================================================================
EOF

#!/bin/bash
# Define ou repõe a palavra-passe de administração no servidor (pedida no terminal, nunca guardada aqui).
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
gcloud compute ssh "$VM" --zone "$ZONE" --quiet -- -t sudo marcaserio-admin

#!/bin/bash
# Descarrega uma cópia consistente da base de dados do servidor para esta pasta.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
OUT="${1:-servidor_$(date +%Y%m%d_%H%M%S).sqlite3}"

ssh_vm "sudo -u marcaserio sqlite3 /var/lib/marcaserio/marcaserio_checkin.sqlite3 '.backup /tmp/marcaserio_download.sqlite3' && sudo chmod 644 /tmp/marcaserio_download.sqlite3"
gcloud compute scp --zone "$ZONE" "$VM":/tmp/marcaserio_download.sqlite3 "$OUT"
ssh_vm "sudo rm -f /tmp/marcaserio_download.sqlite3"
echo "Guardado em $(pwd)/$OUT"

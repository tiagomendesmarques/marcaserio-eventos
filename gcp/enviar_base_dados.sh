#!/bin/bash
# Envia uma base de dados local (p. ex. a da v1.1/v1.2 no Mac) para o servidor.
# A base de dados que lá estiver é guardada antes em copias_seguranca/.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
DB="${1:?Uso: bash gcp/enviar_base_dados.sh caminho/para/marcaserio_checkin.sqlite3}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COPY="$TMP/marcaserio_checkin.sqlite3"
# Cópia consistente, mesmo que a app local esteja aberta.
python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" "$DB" "$COPY"

read -r -p "Substituir a base de dados do servidor por $DB? (escreve SIM) " OK
[ "$OK" = "SIM" ] || exit 1

gcloud compute scp --zone "$ZONE" "$COPY" "$VM":/tmp/marcaserio_upload.sqlite3
ssh_vm 'set -e; D=/var/lib/marcaserio; sudo systemctl stop marcaserio-checkin
  if [ -f $D/marcaserio_checkin.sqlite3 ]; then
    sudo -u marcaserio mkdir -p $D/copias_seguranca
    sudo mv $D/marcaserio_checkin.sqlite3 $D/copias_seguranca/antes_upload_$(date +%Y%m%d_%H%M%S).sqlite3
  fi
  sudo install -o marcaserio -g marcaserio -m 640 /tmp/marcaserio_upload.sqlite3 $D/marcaserio_checkin.sqlite3
  rm /tmp/marcaserio_upload.sqlite3; sudo systemctl start marcaserio-checkin; echo "Base de dados substituída."'

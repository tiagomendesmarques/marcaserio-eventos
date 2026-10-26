#!/bin/bash
# Instala ou atualiza a app na VM. Os dados (/var/lib/marcaserio) não são tocados.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
APP="$(cd .. && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TAR="$TMP/marcaserio.tgz"
COPYFILE_DISABLE=1 tar czf "$TAR" -C "$APP" marcaserio_checkin.py web gcp  # sem ficheiros ._ do macOS

gcloud compute scp --zone "$ZONE" "$TAR" "$VM":/tmp/marcaserio.tgz
ssh_vm "rm -rf /tmp/marcaserio && mkdir /tmp/marcaserio && tar xzf /tmp/marcaserio.tgz -C /tmp/marcaserio \
        && sudo bash /tmp/marcaserio/gcp/servidor_instalar.sh '$DOMAIN' && rm -rf /tmp/marcaserio /tmp/marcaserio.tgz"

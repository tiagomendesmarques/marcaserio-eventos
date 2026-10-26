#!/bin/bash
# Cria a VM (Debian 12) e um snapshot diário do disco, guardado 14 dias.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh

gcloud services enable compute.googleapis.com

gcloud compute instances create "$VM" \
  --zone "$ZONE" --machine-type "$MACHINE" \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 10GB --boot-disk-type pd-balanced \
  --shielded-secure-boot --metadata enable-oslogin=TRUE \
  --no-service-account --no-scopes

REGION="${ZONE%-*}"
gcloud compute resource-policies create snapshot-schedule "$VM-diario" \
  --region "$REGION" --daily-schedule --start-time 04:00 \
  --max-retention-days 14 --on-source-disk-delete keep-auto-snapshots
gcloud compute disks add-resource-policies "$VM" --zone "$ZONE" --resource-policies "$VM-diario"

echo
echo "VM criada. Próximo passo: bash gcp/2_publicar.sh"

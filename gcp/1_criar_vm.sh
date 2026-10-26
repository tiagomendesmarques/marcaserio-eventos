#!/bin/bash
# Cria a VM (Debian 12) e um snapshot diário do disco, guardado 14 dias.
# Pode ser repetido: o que já existir é mantido.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
REGION="${ZONE%-*}"

gcloud services enable compute.googleapis.com

if gcloud compute instances describe "$VM" --zone "$ZONE" >/dev/null 2>&1; then
  echo "A VM $VM já existe."
else
  gcloud compute instances create "$VM" \
    --zone "$ZONE" --machine-type "$MACHINE" \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 10GB --boot-disk-type pd-balanced \
    --shielded-secure-boot --metadata enable-oslogin=TRUE \
    --no-service-account --no-scopes
fi

if ! gcloud compute resource-policies describe "$VM-diario" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute resource-policies create snapshot-schedule "$VM-diario" \
    --region "$REGION" --daily-schedule --start-time 04:00 \
    --max-retention-days 14 --on-source-disk-delete keep-auto-snapshots
fi
gcloud compute disks add-resource-policies "$VM" --zone "$ZONE" --resource-policies "$VM-diario" 2>/dev/null \
  || echo "O snapshot diário já está associado ao disco."

echo
echo "VM pronta. Próximo passo: bash gcp/2_publicar.sh"

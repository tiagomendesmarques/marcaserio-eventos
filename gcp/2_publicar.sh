#!/bin/bash
# Constrói e publica a app no Cloud Run (a primeira vez e em cada atualização). Os dados não são tocados.
set -euo pipefail
source "$(dirname "$0")/config.sh"
cd "$REPO"

gcloud run deploy "$SERVICE" --source . --region "$REGION" \
  --service-account "$SA" --allow-unauthenticated \
  --set-env-vars "MA_STORE=firestore,MA_FIRESTORE_DB=$DATABASE,MA_PRINT_MODE=agente,MA_PUBLIC_URL=https://$DOMAIN,MA_TRUST_PROXY=google,MA_HOST=0.0.0.0,MA_OPEN_BROWSER=0,MA_VERSION=manual-$(date +%Y%m%d-%H%M)" \
  --min-instances 0 --max-instances 3 --cpu 1 --memory 512Mi --concurrency 40 --timeout 60 \
  --quiet

echo
echo "Publicado: $(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

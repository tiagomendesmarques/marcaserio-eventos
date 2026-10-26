#!/bin/bash
# Constrói e publica a app no Cloud Run (a primeira vez e em cada atualização). Os dados não são tocados.
set -euo pipefail
source "$(dirname "$0")/config.sh"
cd "$REPO"

# Login com Google: as credenciais OAuth vêm do Secret Manager (ver gcp/5_login_google.sh).
SECRETS=()
if gcloud secrets describe marcaserio-google-client-secret >/dev/null 2>&1; then
  SECRETS=(--set-secrets "MA_GOOGLE_CLIENT_ID=marcaserio-google-client-id:latest,MA_GOOGLE_CLIENT_SECRET=marcaserio-google-client-secret:latest")
else
  echo "Aviso: o login com Google ainda não está configurado (bash gcp/5_login_google.sh)."
fi

gcloud run deploy "$SERVICE" --source . --region "$REGION" "${SECRETS[@]}" \
  --service-account "$SA" --allow-unauthenticated \
  --set-env-vars "MA_STORE=firestore,MA_FIRESTORE_DB=$DATABASE,MA_PRINT_MODE=agente,MA_PUBLIC_URL=https://$DOMAIN,MA_TRUST_PROXY=google,MA_HOST=0.0.0.0,MA_OPEN_BROWSER=0,MA_VERSION=manual-$(date +%Y%m%d-%H%M)" \
  --min-instances 0 --max-instances 3 --cpu 1 --memory 512Mi --concurrency 40 --timeout 60 \
  --quiet

echo
echo "Publicado: $(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

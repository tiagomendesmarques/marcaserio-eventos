#!/bin/bash
# Ativa o login com Google (contas @marcaserio.pt). Antes, cria o cliente OAuth na consola:
#   Google Auth Platform → Clients → Create client → Web application
#   Authorized redirect URI: https://eventos.marcaserio.pt/auth/google/callback
# Guarda o Client ID e o Client secret no Secret Manager (na UE) e volta a publicar a app.
set -euo pipefail
source "$(dirname "$0")/config.sh"

gcloud services enable secretmanager.googleapis.com
read -r -p "Client ID (termina em .apps.googleusercontent.com): " CLIENT_ID
read -r -s -p "Client secret (não aparece enquanto escreves): " CLIENT_SECRET; echo
[[ "$CLIENT_ID" == *.apps.googleusercontent.com ]] || { echo "Client ID inválido." >&2; exit 1; }
[ -n "$CLIENT_SECRET" ] || { echo "Client secret em falta." >&2; exit 1; }

put_secret() {
  if ! gcloud secrets describe "$1" >/dev/null 2>&1; then
    gcloud secrets create "$1" --replication-policy=user-managed --locations="$REGION" >/dev/null
  fi
  printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null
  # Só a app (a sua conta de serviço) consegue ler o segredo.
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor --quiet >/dev/null
}
put_secret marcaserio-google-client-id "$CLIENT_ID"
put_secret marcaserio-google-client-secret "$CLIENT_SECRET"
echo "Credenciais guardadas no Secret Manager. A publicar a app com o login com Google…"

bash "$REPO/gcp/2_publicar.sh"

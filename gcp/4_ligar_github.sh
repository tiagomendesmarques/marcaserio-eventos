#!/bin/bash
# Liga o GitHub ao Google Cloud para publicar a cada push (uma vez, depois do 2_publicar.sh).
# Sem chaves: o Google passa a confiar em pedidos assinados pelo GitHub que venham deste
# repositório e do ramo main (Workload Identity Federation).
# A conta marcaserio-deployer só pode enviar imagens para o seu repositório e atualizar o
# serviço marcaserio-eventos; não tem acesso a mais nada do projeto.
set -euo pipefail
source "$(dirname "$0")/config.sh"
GITHUB_REPO="${GITHUB_REPO:-tiagomendesmarques/marcaserio-eventos}"
DEPLOYER_NAME="${DEPLOYER_NAME:-marcaserio-deployer}"
DEPLOYER="$DEPLOYER_NAME@$PROJECT.iam.gserviceaccount.com"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-marcaserio-eventos}"
AR_REPO="${AR_REPO:-marcaserio-eventos}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

if ! gcloud run services describe "$SERVICE" --region "$REGION" >/dev/null 2>&1; then
  echo "O serviço $SERVICE ainda não existe. Corre primeiro: bash gcp/2_publicar.sh" >&2
  exit 1
fi

gcloud services enable iamcredentials.googleapis.com sts.googleapis.com artifactregistry.googleapis.com

# Repositório de imagens, guardando só as 10 versões mais recentes.
if ! gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" --repository-format docker --location "$REGION" \
    --description "Imagens do marcAsério Check-in (publicadas pelo GitHub Actions)"
fi
POLICY="$(mktemp)"
cat > "$POLICY" <<'JSON'
[{"name": "manter-10-recentes", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 10}},
 {"name": "apagar-as-restantes", "action": {"type": "Delete"}, "condition": {"tagState": "any"}}]
JSON
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" --policy "$POLICY" --no-dry-run >/dev/null
rm -f "$POLICY"

if ! gcloud iam service-accounts describe "$DEPLOYER" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$DEPLOYER_NAME" --display-name "marcAsério Check-in: deploy pelo GitHub"
fi

if ! gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
fi
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool "$POOL" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global --workload-identity-pool "$POOL" \
    --display-name "GitHub: $GITHUB_REPO" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"
fi

# Só este repositório pode usar a conta de deploy.
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO" \
  --quiet >/dev/null
# Permissões mínimas: enviar imagens, atualizar este serviço e executá-lo com a sua conta.
gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location "$REGION" \
  --member "serviceAccount:$DEPLOYER" --role roles/artifactregistry.writer --quiet >/dev/null
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --member "serviceAccount:$DEPLOYER" --role roles/run.developer --quiet >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --member "serviceAccount:$DEPLOYER" --role roles/iam.serviceAccountUser --quiet >/dev/null

cat <<MSG

Ligação criada. Falta criar 3 variáveis no GitHub:
  https://github.com/$GITHUB_REPO/settings/variables/actions  →  New repository variable

  GCP_PROJECT_ID     $PROJECT
  GCP_WIF_PROVIDER   projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER
  GCP_DEPLOYER_SA    $DEPLOYER

Depois: Actions → «Publicar no Cloud Run» → Run workflow, para testar.
MSG

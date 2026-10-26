#!/bin/bash
# Prepara o projeto: APIs, base de dados Firestore própria com cópia diária e conta de serviço.
# Pode ser repetido: o que já existir é mantido. Não altera nada do que já existe no projeto.
set -euo pipefail
source "$(dirname "$0")/config.sh"

gcloud services enable run.googleapis.com firestore.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

if gcloud firestore databases describe --database="$DATABASE" >/dev/null 2>&1; then
  echo "A base de dados Firestore $DATABASE já existe."
else
  gcloud firestore databases create --database="$DATABASE" --location="$REGION" \
    --type=firestore-native --delete-protection
fi

if [ -z "$(gcloud firestore backups schedules list --database="$DATABASE" --format='value(name)')" ]; then
  gcloud firestore backups schedules create --database="$DATABASE" --recurrence=daily --retention=14d
fi

if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="marcAsério Check-in (Cloud Run)"
fi
# Acesso apenas à base de dados deste serviço, não a outras do projeto.
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/datastore.user \
  --condition="expression=resource.name==\"projects/$PROJECT/databases/$DATABASE\",title=apenas-$DATABASE" \
  --quiet >/dev/null

echo
echo "Projeto preparado. Próximo passo: bash gcp/2_publicar.sh"

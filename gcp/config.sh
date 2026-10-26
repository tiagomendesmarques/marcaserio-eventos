# Configuração partilhada pelos scripts (correm no Cloud Shell ou num terminal com gcloud).
# O projeto vem, por esta ordem, de: PROJECT=..., do projeto ativo no Cloud Shell, ou do gcloud.
PROJECT="${PROJECT:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}"
REGION="${REGION:-europe-west1}"           # Bélgica: UE e com mapeamento de domínios no Cloud Run
SERVICE="${SERVICE:-marcaserio-eventos}"    # serviço Cloud Run
DATABASE="${DATABASE:-marcaserio-eventos}"  # base de dados Firestore própria (não é a «(default)»)
SA_NAME="${SA_NAME:-marcaserio-eventos}"    # conta de serviço do Cloud Run
DOMAIN="${DOMAIN:-eventos.marcaserio.pt}"

if [ -z "$PROJECT" ]; then
  echo "Projeto GCP não definido. Usa: PROJECT=id-do-projeto bash gcp/<script>.sh" >&2
  exit 1
fi
gcloud config set project "$PROJECT" --quiet >/dev/null
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
ROOT_DOMAIN="${DOMAIN#*.}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Projeto: $PROJECT · Região: $REGION · Serviço: $SERVICE · Firestore: $DATABASE · Domínio: $DOMAIN"

# Python com o cliente do Firestore, para os scripts que falam diretamente com a base de dados.
python_firestore() {
  local venv="$HOME/.marcaserio-venv"
  [ -x "$venv/bin/python" ] || python3 -m venv "$venv"
  "$venv/bin/pip" install -q -r "$REPO/requirements.txt"
  MA_STORE=firestore MA_FIRESTORE_DB="$DATABASE" MA_PRINT_MODE=agente GOOGLE_CLOUD_PROJECT="$PROJECT" \
    "$venv/bin/python" "$@"
}

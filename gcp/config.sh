# Configuração partilhada pelos scripts.
# O projeto vem, por esta ordem, de: PROJECT=..., do projeto ativo no Cloud Shell, ou do gcloud.
PROJECT="${PROJECT:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}"
ZONE="${ZONE:-europe-southwest1-a}"      # Madrid: dados dos participantes na UE, perto de Portugal
VM="${VM:-marcaserio-eventos}"
MACHINE="${MACHINE:-e2-micro}"
DOMAIN="${DOMAIN:-eventos.marcaserio.pt}"

if [ -z "$PROJECT" ]; then
  echo "Projeto GCP não definido. Usa: PROJECT=id-do-projeto bash gcp/<script>.sh" >&2
  exit 1
fi
gcloud config set project "$PROJECT" --quiet >/dev/null
echo "Projeto: $PROJECT · VM: $VM ($ZONE) · Domínio: $DOMAIN"

# --quiet: cria a chave SSH sem pedir frase-passe na primeira utilização.
ssh_vm() { gcloud compute ssh "$VM" --zone "$ZONE" --quiet --command "$1"; }
scp_vm() { gcloud compute scp --zone "$ZONE" --quiet "$@"; }

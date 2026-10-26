# Configuração partilhada pelos scripts. Edita PROJECT antes de começar.
PROJECT="${PROJECT:-o-teu-projeto-gcp}"
ZONE="${ZONE:-europe-southwest1-a}"      # Madrid: dados dos participantes na UE, perto de Portugal
VM="${VM:-marcaserio-eventos}"
MACHINE="${MACHINE:-e2-micro}"
DOMAIN="${DOMAIN:-eventos.marcaserio.pt}"

if [ "$PROJECT" = "o-teu-projeto-gcp" ]; then
  echo "Edita gcp/config.sh e define PROJECT com o ID do teu projeto GCP." >&2
  exit 1
fi
gcloud config set project "$PROJECT" --quiet >/dev/null
ssh_vm() { gcloud compute ssh "$VM" --zone "$ZONE" --command "$1"; }

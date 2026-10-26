#!/bin/bash
# Liga eventos.marcaserio.pt ao Cloud Run. Só cria o registo «eventos» na Cloudflare (feito à mão
# no painel); o registo A de marcaserio.pt não é tocado.
set -euo pipefail
source "$(dirname "$0")/config.sh"

if ! gcloud domains list-user-verified --format='value(id)' | grep -qx "$ROOT_DOMAIN"; then
  echo "1) Falta verificar $ROOT_DOMAIN na Google Search Console."
  echo "   A Search Console vai pedir um registo TXT na Cloudflare (é um TXT novo; o registo A fica como está)."
  gcloud domains verify "$ROOT_DOMAIN"
  echo "   Depois de verificado, corre de novo: bash gcp/3_dominio.sh"
  exit 0
fi

if ! gcloud beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1; then
  gcloud beta run domain-mappings create --service "$SERVICE" --domain "$DOMAIN" --region "$REGION"
fi

echo
echo "2) Na Cloudflare (zona $ROOT_DOMAIN → DNS), cria este registo:"
gcloud beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" \
  --format='table[box](status.resourceRecords[].name,status.resourceRecords[].type,status.resourceRecords[].rrdata)'
echo "   Proxy status: DNS only (nuvem CINZENTA). O certificado HTTPS é emitido pela Google em 15-60 min."

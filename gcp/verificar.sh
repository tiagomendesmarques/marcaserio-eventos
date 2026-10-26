#!/bin/bash
# Estado do serviço e do domínio. Mostra também marcaserio.pt, que não deve mudar.
set -uo pipefail
source "$(dirname "$0")/config.sh"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)' 2>/dev/null)"
check() { printf "%-42s %s\n" "$1" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$1")"; }
echo; echo "== HTTP"
[ -n "$URL" ] && check "$URL/" || echo "Serviço $SERVICE não encontrado em $REGION."
check "https://$DOMAIN/"
check "https://$ROOT_DOMAIN/"
echo; echo "== Mapeamento de domínio"
gcloud beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" \
  --format='value(status.conditions[].type,status.conditions[].status)' 2>/dev/null || echo "(ainda não criado)"
echo; echo "== DNS"
for name in "$DOMAIN" "$ROOT_DOMAIN"; do printf "%-24s %s\n" "$name" "$(getent hosts "$name" | awk '{print $1}' | paste -sd' ')"; done

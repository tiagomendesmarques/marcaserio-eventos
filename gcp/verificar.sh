#!/bin/bash
# Verifica o servidor e o domínio. Mostra também o estado de marcaserio.pt, que não deve mudar.
set -uo pipefail
cd "$(dirname "$0")"
source config.sh
ROOT="${DOMAIN#*.}"

echo; echo "== Serviços na VM"
ssh_vm 'for s in marcaserio-checkin cloudflared; do printf "%-20s %s\n" "$s" "$(systemctl is-active $s)"; done
        printf "%-20s %s\n" "app local" "$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8770/)"'
echo; echo "== Público"
for url in "https://$DOMAIN/" "https://$ROOT/"; do
  printf "%-32s %s\n" "$url" "$(curl -s -o /dev/null -w '%{http_code} → %{redirect_url}' --max-time 15 "$url")"
done
echo; echo "== DNS de $DOMAIN"
getent hosts "$DOMAIN" || echo "(ainda sem resolução)"

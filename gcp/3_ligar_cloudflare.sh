#!/bin/bash
# Liga a VM ao Cloudflare Tunnel com o token copiado do painel Zero Trust.
set -euo pipefail
cd "$(dirname "$0")"
source config.sh
TOKEN="${1:?Uso: bash gcp/3_ligar_cloudflare.sh <TOKEN_DO_TUNNEL>}"

ssh_vm "sudo cloudflared service uninstall >/dev/null 2>&1 || true; sudo cloudflared service install '$TOKEN' && sudo systemctl --no-pager status cloudflared | head -5"
echo
echo "Feito. Confirma no painel da Cloudflare que o túnel aparece como HEALTHY e abre https://$DOMAIN"

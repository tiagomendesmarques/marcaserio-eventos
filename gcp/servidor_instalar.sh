#!/bin/bash
# Corre NA VM, como root (chamado por 2_publicar.sh). Pode ser repetido para atualizar.
set -euo pipefail
DOMAIN="${1:?domínio em falta}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/marcaserio
DATA=/var/lib/marcaserio

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 sqlite3 curl >/dev/null

id marcaserio >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin marcaserio
install -d -o marcaserio -g marcaserio -m 750 "$DATA"

rm -rf "$APP.new"
mkdir -p "$APP.new"
cp -r "$SRC/marcaserio_checkin.py" "$SRC/web" "$APP.new/"
rm -rf "$APP"
mv "$APP.new" "$APP"

sed "s|__DOMAIN__|$DOMAIN|" "$SRC/gcp/marcaserio-checkin.service" > /etc/systemd/system/marcaserio-checkin.service

cat > /usr/local/bin/marcaserio-admin <<'SH'
#!/bin/bash
# Define ou repõe a palavra-passe de administração.
exec sudo -u marcaserio env MA_DATA_DIR=/var/lib/marcaserio python3 /opt/marcaserio/marcaserio_checkin.py --definir-admin
SH
chmod 755 /usr/local/bin/marcaserio-admin

systemctl daemon-reload
systemctl enable marcaserio-checkin >/dev/null 2>&1
systemctl restart marcaserio-checkin

if ! command -v cloudflared >/dev/null; then
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared >/dev/null
fi

sleep 1
systemctl --no-pager --lines=3 status marcaserio-checkin
echo
echo "App instalada em $APP; dados em $DATA."

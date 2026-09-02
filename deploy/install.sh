#!/usr/bin/env bash
# Inštalácia/aktualizácia trénera + Radicale VNÚTRI LXC kontajnera (Debian 12/13), ako root.
#   SRC_DIR=/opt/trener-src bash deploy/install.sh
# Idempotentné: existujúce .env, users a dáta sa nikdy neprepisujú.
set -euo pipefail
SRC_DIR="${SRC_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
APP_DIR=/opt/trener
CFG_DIR=/etc/trener
RAD_DIR=/opt/radicale
RAD_CFG=/etc/radicale
RADICALE_VERSION="${RADICALE_VERSION:-3.5.*}"

echo ">> systémové balíky"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip ca-certificates curl rsync >/dev/null

echo ">> používatelia a adresáre"
id trener &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin trener
id radicale &>/dev/null || useradd --system --home /var/lib/radicale --shell /usr/sbin/nologin radicale
mkdir -p "$APP_DIR" "$CFG_DIR" /var/lib/trener /var/log/trener "$RAD_DIR" "$RAD_CFG" /var/lib/radicale/collections

echo ">> kód trénera → $APP_DIR"
rsync -a --delete --exclude '.venv' --exclude '.git' --exclude '__pycache__' --exclude '.pytest_cache' "$SRC_DIR"/ "$APP_DIR"/
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"

echo ">> Radicale (pip) → $RAD_DIR"
[ -d "$RAD_DIR/.venv" ] || python3 -m venv "$RAD_DIR/.venv"
"$RAD_DIR/.venv/bin/pip" install -q --upgrade pip
"$RAD_DIR/.venv/bin/pip" install -q "radicale==$RADICALE_VERSION" bcrypt passlib
[ -f "$RAD_CFG/config" ] || cp "$APP_DIR/deploy/radicale.config" "$RAD_CFG/config"
if [ ! -f "$RAD_CFG/users" ]; then
  : "${RADICALE_USER:=jakub}"
  : "${RADICALE_PASSWORD:=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)}"
  HASH=$("$RAD_DIR/.venv/bin/python" -c "import bcrypt,sys;print(bcrypt.hashpw(sys.argv[1].encode(),bcrypt.gensalt()).decode())" "$RADICALE_PASSWORD")
  echo "$RADICALE_USER:$HASH" > "$RAD_CFG/users"
  echo "   Radicale používateľ: $RADICALE_USER  heslo: $RADICALE_PASSWORD  (zapíš si ho – je aj v $CFG_DIR/trener.env)"
  echo "$RADICALE_PASSWORD" > "$RAD_CFG/.password-initial"; chmod 600 "$RAD_CFG/.password-initial"
fi
chown -R radicale:radicale /var/lib/radicale
chown root:radicale "$RAD_CFG/users" "$RAD_CFG/config"; chmod 640 "$RAD_CFG/users" "$RAD_CFG/config"

echo ">> konfigurácia trénera"
if [ ! -f "$CFG_DIR/trener.env" ]; then
  cp "$APP_DIR/deploy/trener.env.example" "$CFG_DIR/trener.env"
  if [ -f "$RAD_CFG/.password-initial" ]; then
    sed -i "s|^CALDAV_PASSWORD=.*|CALDAV_PASSWORD=$(cat "$RAD_CFG/.password-initial")|" "$CFG_DIR/trener.env"
  fi
  sed -i "s|^WAKE_WEBHOOK_TOKEN=.*|WAKE_WEBHOOK_TOKEN=$(openssl rand -hex 24)|" "$CFG_DIR/trener.env"
  echo "   vytvorený $CFG_DIR/trener.env – DOPLŇ BOT_TOKEN, OWNER_CHAT_ID, SMB_PASSWORD"
fi
chown -R trener:trener "$APP_DIR" /var/lib/trener /var/log/trener "$CFG_DIR"
chmod 600 "$CFG_DIR/trener.env"

echo ">> systemd"
cp "$APP_DIR/deploy/trener.service" /etc/systemd/system/trener.service
cp "$APP_DIR/deploy/radicale.service" /etc/systemd/system/radicale.service
systemctl daemon-reload
systemctl enable radicale trener >/dev/null
systemctl restart radicale
sleep 1
if [ -n "${NO_START:-}" ]; then
  echo "   NO_START nastavené – trener nespúšťam (spusti: systemctl start trener)"
else
  systemctl restart trener || true
fi
echo
echo "Hotovo. Kontrola:"
echo "  systemctl status radicale trener --no-pager"
echo "  journalctl -u trener -f"
echo "  curl -s http://localhost:8790/health"

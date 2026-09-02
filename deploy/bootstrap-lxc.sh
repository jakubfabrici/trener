#!/usr/bin/env bash
# Spúšťa sa NA PROXMOXE (pve) ako root. Vytvorí LXC (ak neexistuje), nahrá zdrojáky
# z tohto checkoutu a spustí deploy/install.sh vnútri. Idempotentné.
#   VMID=122 IP_CIDR=192.168.1.254/24 bash deploy/bootstrap-lxc.sh
set -euo pipefail
VMID="${VMID:-122}"
HOSTNAME_="${HOSTNAME_:-trener}"
IP_CIDR="${IP_CIDR:-192.168.1.254/24}"
GATEWAY="${GATEWAY:-192.168.1.1}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"
DISK_GB="${DISK_GB:-4}"
MEM_MB="${MEM_MB:-512}"
TEMPLATE="${TEMPLATE:-$(pveam list local | awk '/debian-12-standard.*amd64/{print $1}' | sort -V | tail -1)}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say() { printf '\n\033[1;36m>> %s\033[0m\n' "$*"; }

if pct status "$VMID" &>/dev/null; then
  say "LXC $VMID už existuje – použijem ho"
else
  say "vytváram LXC $VMID ($HOSTNAME_, $IP_CIDR) z $TEMPLATE"
  pct create "$VMID" "$TEMPLATE" --hostname "$HOSTNAME_" --cores 1 --memory "$MEM_MB" --swap 256 \
    --rootfs "${STORAGE}:${DISK_GB}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CIDR},gw=${GATEWAY}" \
    --nameserver "$GATEWAY" --features nesting=1 --unprivileged 1 --onboot 1 \
    --timezone Europe/Bratislava \
    --description "Virtualny trener klikov v2 (Telegram + Apple Pripomienky/Radicale + tabulka na OMV)"
fi
pct status "$VMID" | grep -q running || pct start "$VMID"
inct() { pct exec "$VMID" -- bash -lc "$*"; }
say "čakám na sieť v kontajneri"
for _ in $(seq 1 40); do inct "getent hosts deb.debian.org >/dev/null 2>&1" && break; sleep 3; done

say "nahrávam zdrojáky a inštalujem"
TARBALL=/tmp/trener-src.tar.gz
tar -C "$REPO_DIR" --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache -czf "$TARBALL" .
inct "rm -rf /opt/trener-src && mkdir -p /opt/trener-src"
pct push "$VMID" "$TARBALL" /tmp/trener-src.tar.gz
inct "tar -C /opt/trener-src -xzf /tmp/trener-src.tar.gz && rm -f /tmp/trener-src.tar.gz"
inct "RADICALE_USER='${RADICALE_USER:-jakub}' RADICALE_PASSWORD='${RADICALE_PASSWORD:-}' SRC_DIR=/opt/trener-src bash /opt/trener-src/deploy/install.sh"
rm -f "$TARBALL"
say "HOTOVO – ďalej: doplň /etc/trener/trener.env v kontajneri a reštartni: pct exec $VMID -- systemctl restart trener"

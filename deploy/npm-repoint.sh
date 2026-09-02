#!/usr/bin/env bash
# Spúšťa sa V LXC Nginx Proxy Managera (192.168.1.213) ako root: presmeruje proxy hosty
# kliky.fabrici.xyz (id 27 → :8790) a kliky-cal.fabrici.xyz (id 28 → :5232) na novú IP.
#   NEW_IP=192.168.1.254 bash npm-repoint.sh
set -euo pipefail
NEW_IP="${NEW_IP:?NEW_IP chýba}"
DB=/data/database.sqlite
cp -a "$DB" "$DB.bak-$(date +%Y%m%d-%H%M%S)-trener"
python3 - "$NEW_IP" <<'PY'
import sqlite3, sys
ip = sys.argv[1]
c = sqlite3.connect("/data/database.sqlite")
rows = c.execute("select id, domain_names, forward_host, forward_port from proxy_host where is_deleted=0 and (domain_names like '%kliky.fabrici.xyz%' or domain_names like '%kliky-cal.fabrici.xyz%')").fetchall()
for r in rows:
    print("pred:", r)
    c.execute("update proxy_host set forward_host=?, modified_on=datetime('now') where id=?", (ip, r[0]))
c.commit()
for r in c.execute("select id, domain_names, forward_host, forward_port from proxy_host where id in (%s)" % ",".join(str(x[0]) for x in rows)):
    print("po:  ", r)
PY
for f in /data/nginx/proxy_host/27.conf /data/nginx/proxy_host/28.conf; do
  [ -f "$f" ] || continue
  cp -a "$f" "$f.bak-trener"
  sed -i -E "s/set \\\$server +\"[0-9.]+\";/set \$server \"$NEW_IP\";/" "$f"
  grep -n 'set $server' "$f"
done
nginx -t && nginx -s reload && echo "nginx reloaded"

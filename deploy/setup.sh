#!/usr/bin/env bash
# Instalación / actualización en Lightsail (Ubuntu/Debian). Correr como root desde el repo clonado:
#   sudo bash deploy/setup.sh
set -euo pipefail
APP=/opt/bitacora-mci
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v python3 >/dev/null || ! python3 -c 'import venv' 2>/dev/null; then
  apt-get update && apt-get install -y python3 python3-venv
fi
command -v nginx >/dev/null || apt-get install -y nginx
command -v rsync >/dev/null || apt-get install -y rsync

id bitacora >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin bitacora
mkdir -p "$APP"
if [ "$SRC" != "$APP" ]; then
  rsync -a --delete --exclude .env --exclude .venv --exclude .git "$SRC"/ "$APP"/
fi
[ -f "$APP/.env" ] || { cp "$APP/.env.example" "$APP/.env"; chmod 600 "$APP/.env"; echo ">> Edita $APP/.env y vuelve a correr este script."; }

python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R bitacora:bitacora "$APP"
chmod 600 "$APP/.env"

# Migraciones
sudo -u bitacora bash -c "cd $APP && set -a && . ./.env && set +a && .venv/bin/alembic upgrade head"

# Servicios
cp "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bitacora.service
for t in informe-diario recordatorios ejecutar-acciones sync-cfdi; do systemctl enable --now "bitacora-$t.timer"; done
systemctl restart bitacora.service

# Nginx
if [ ! -f /etc/nginx/sites-available/bitacora ]; then
  cp "$APP/deploy/nginx-bitacora.conf" /etc/nginx/sites-available/bitacora
  ln -sf /etc/nginx/sites-available/bitacora /etc/nginx/sites-enabled/bitacora
fi
nginx -t && systemctl reload nginx
echo ">> Listo. Revisa: systemctl status bitacora; journalctl -u bitacora -f"

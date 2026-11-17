#!/usr/bin/env bash
# Автообновление бота с GitHub.
#   bash scripts/autoupdate.sh            — проверить и обновить один раз
#   bash scripts/autoupdate.sh --install  — включить проверку каждые 5 минут (cron)
#   bash scripts/autoupdate.sh --remove   — выключить автообновление
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(pwd)
# cron запускается с сокращённым PATH.
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
LOG="$DIR/data/autoupdate.log"
DEPLOYED="$DIR/data/autoupdate.deployed"
CRON_LINE="*/5 * * * * /bin/bash \"$DIR/scripts/autoupdate.sh\" >> \"$LOG\" 2>&1"

case "${1:-}" in
  --install)
    command -v crontab >/dev/null || { echo "❌ Не найден crontab. Установите cron."; exit 1; }
    command -v flock >/dev/null || { echo "❌ Не найден flock. Установите util-linux."; exit 1; }
    docker compose version >/dev/null
    git rev-parse --abbrev-ref '@{u}' >/dev/null
    mkdir -p "$DIR/data"
    chmod +x "$DIR/scripts/autoupdate.sh"
    ( crontab -l 2>/dev/null | grep -v "scripts/autoupdate.sh" || true; echo "$CRON_LINE" ) | crontab -
    echo "✅ Автообновление включено: проверка GitHub каждые 5 минут."
    echo "Журнал: $LOG"
    exit 0 ;;
  --remove)
    ( crontab -l 2>/dev/null | grep -v "scripts/autoupdate.sh" || true ) | crontab -
    echo "Автообновление выключено."
    exit 0 ;;
esac

# одна проверка за раз, даже если предыдущая сборка ещё идёт
mkdir -p "$DIR/data"
exec 9>"$DIR/data/autoupdate.lock"
flock -n 9 || exit 0

# Не затираем ручные изменения на сервере.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "$(date '+%F %T') ❌ Есть локальные изменения; обновление пропущено."
  exit 1
fi
git fetch -q origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse '@{u}')
LAST_DEPLOYED=$(cat "$DEPLOYED" 2>/dev/null || true)
[ "$LOCAL" = "$REMOTE" ] && [ "$LOCAL" = "$LAST_DEPLOYED" ] && exit 0
if ! git merge-base --is-ancestor "$LOCAL" "$REMOTE"; then
  echo "$(date '+%F %T') ❌ История сервера отличается от GitHub; требуется ручная проверка."
  exit 1
fi

echo "$(date '+%F %T') обновление ${LOCAL:0:7} -> ${REMOTE:0:7}"
git pull -q --ff-only
# Сначала собираем образ: при ошибке работающий контейнер остаётся запущен.
docker compose build
docker compose up -d
# Отмечаем только успешный запуск. При сбое cron повторит сборку, даже если pull уже прошёл.
printf '%s\n' "$(git rev-parse HEAD)" > "$DEPLOYED"
echo "$(date '+%F %T') готово"

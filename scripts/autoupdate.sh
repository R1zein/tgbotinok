#!/usr/bin/env bash
# Автообновление бота с GitHub.
#   bash scripts/autoupdate.sh            — проверить и обновить один раз
#   bash scripts/autoupdate.sh --install  — включить проверку каждые 5 минут (cron)
#   bash scripts/autoupdate.sh --remove   — выключить автообновление
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=$(pwd)
LOG="$DIR/data/autoupdate.log"
CRON_LINE="*/5 * * * * $DIR/scripts/autoupdate.sh >> $LOG 2>&1"

case "${1:-}" in
  --install)
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
exec 9>/tmp/tgbotinok-autoupdate.lock
flock -n 9 || exit 0

git fetch -q origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse '@{u}')
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "$(date '+%F %T') обновление ${LOCAL:0:7} -> ${REMOTE:0:7}"
git pull -q --ff-only
docker compose up -d --build
echo "$(date '+%F %T') готово"

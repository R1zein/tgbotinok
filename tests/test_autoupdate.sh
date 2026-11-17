#!/usr/bin/env bash
# Изолированная проверка обновлений без GitHub и Docker.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT
export TEST_DIR
mkdir -p "$TEST_DIR/project with spaces/scripts" "$TEST_DIR/project with spaces/data"
cp "$ROOT/scripts/autoupdate.sh" "$TEST_DIR/project with spaces/scripts/"
SCRIPT="$TEST_DIR/project with spaces/scripts/autoupdate.sh"
MARKER="$TEST_DIR/project with spaces/data/autoupdate.deployed"
printf 'old\n' > "$TEST_DIR/head"
git() {
    case "$1" in
        status) printf '%s' "${DIRTY:-}" ;;
        fetch) return 0 ;;
        rev-parse)
            if [ "$2" = HEAD ]; then cat "$TEST_DIR/head"; else echo new; fi ;;
        merge-base) [ "${DIVERGED:-0}" = 0 ] ;;
        pull) echo new > "$TEST_DIR/head" ;;
        *) return 99 ;;
    esac
}
docker() {
    echo "$*" >> "$TEST_DIR/docker_calls"
    if [ "$2" = build ] && [ "${FAIL_BUILD:-0}" = 1 ]; then return 1; fi
}
flock() { return 0; }
export -f git docker flock

# Pull прошёл, сборка упала: старый контейнер не перезапускается, отметки нет.
if FAIL_BUILD=1 bash "$SCRIPT"; then echo 'Expected build failure'; exit 1; fi
[ "$(cat "$TEST_DIR/head")" = new ]
[ ! -f "$MARKER" ]
! grep -q 'up -d' "$TEST_DIR/docker_calls"

# HEAD уже равен upstream, но неудачная сборка всё равно повторяется.
bash "$SCRIPT"
[ "$(cat "$MARKER")" = new ]
[ "$(grep -c 'compose build' "$TEST_DIR/docker_calls")" = 2 ]
grep -q 'compose up -d' "$TEST_DIR/docker_calls"

# Успешно развёрнутый коммит не пересобирается повторно.
BEFORE=$(wc -l < "$TEST_DIR/docker_calls")
bash "$SCRIPT"
[ "$(wc -l < "$TEST_DIR/docker_calls")" = "$BEFORE" ]

if DIRTY=' M bot/config.py' bash "$SCRIPT"; then exit 1; fi
echo old > "$TEST_DIR/head"
if DIVERGED=1 bash "$SCRIPT"; then exit 1; fi
[ "$(cat "$TEST_DIR/head")" = old ]
[ "$(wc -l < "$TEST_DIR/docker_calls")" = "$BEFORE" ]
echo 'autoupdate tests passed'

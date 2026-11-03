#!/usr/bin/env bash
# Пошаговая настройка и запуск бота на сервере (Ubuntu/Debian).
# Запуск: bash setup.sh   — можно повторять, уже заполненные значения сохраняются.
set -euo pipefail
cd "$(dirname "$0")"

ask() {  # ask "Вопрос" ПЕРЕМЕННАЯ
    local prompt=$1 key=$2 current answer
    current=$(grep -E "^$key=" .env | head -n1 | cut -d= -f2- || true)
    if [ -n "$current" ]; then
        read -rp "$prompt [Enter — оставить текущее]: " answer < /dev/tty
        answer=${answer:-$current}
    else
        read -rp "$prompt: " answer < /dev/tty
    fi
    set_var "$key" "$(echo "$answer" | tr -d '[:space:]')"
}

set_var() {  # безопасная запись KEY=VALUE в .env без экранирования
    local tmp
    tmp=$(mktemp)
    grep -vE "^$1=" .env > "$tmp" || true
    echo "$1=$2" >> "$tmp"
    mv "$tmp" .env
    chmod 600 .env
}

echo "=== 1/4 Проверка Docker ==="
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден — устанавливаю (1–2 минуты)…"
    curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null

echo
echo "=== 2/4 Настройки бота ==="
[ -f .env ] || cp .env.example .env
ask "Токен бота от @BotFather" BOT_TOKEN
ask "Ваш Telegram ID (узнать: @userinfobot)" ADMIN_IDS

echo
echo "=== 3/4 Юзербот (подарки и юзернеймы) ==="
read -rp "Настроить юзербота сейчас? (y/n): " ub < /dev/tty
if [[ "$ub" =~ ^[YyДд] ]]; then
    ask "API_ID с my.telegram.org" API_ID
    ask "API_HASH с my.telegram.org" API_HASH
    current=$(grep -E "^USERBOT_SESSION=" .env | cut -d= -f2- || true)
    relogin=y
    if [ -n "$current" ]; then
        read -rp "Сессия уже есть. Войти заново? (y/n): " relogin < /dev/tty
    fi
    if [[ "$relogin" =~ ^[YyДд] ]]; then
        echo "Сейчас попросят телефон аккаунта (+7...), код из Telegram и облачный пароль, если он есть."
        mkdir -p data
        rm -f data/.session
        docker compose build -q
        docker compose run --rm -e SESSION_OUT=/app/data/.session bot python -m bot.services.login < /dev/tty
        set_var USERBOT_SESSION "$(cat data/.session)"
        rm -f data/.session
        echo "✅ Сессия юзербота сохранена в .env"
    fi
else
    echo "Пропущено. Подарки и юзернеймы будут недоступны — можно запустить скрипт позже."
fi

echo
echo "=== 4/4 Запуск ==="
docker compose up -d --build
echo
echo "✅ Бот запущен и будет сам подниматься после перезагрузки сервера."
echo "Логи: docker compose logs -f --tail 50   (выход — Ctrl+C, бот продолжит работать)"

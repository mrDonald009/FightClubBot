#!/usr/bin/env bash
# Сравнить DATABASE_URL тестового и боевого деплоя на сервере.
# Использование:
#   bash scripts/server_compare_database_urls.sh
#   bash scripts/server_compare_database_urls.sh /home/cat/FightClubBot-dev /home/cat/FightClubBot
#
# Ожидаются файлы .env в корне каждого каталога. Если бот запускается через systemd/docker
# с другим файлом окружения — укажите тот же путь вручную или подставьте свой способ чтения.

set -euo pipefail

DEV_DIR="${1:-${FIGHTCLUB_DEV_DIR:-$HOME/FightClubBot-dev}}"
PROD_DIR="${2:-${FIGHTCLUB_PROD_DIR:-$HOME/FightClubBot}}"

extract_url() {
  local f="$1/.env"
  if [[ -f "$f" ]]; then
    grep -E '^[[:space:]]*DATABASE_URL=' "$f" | head -1 | sed 's/^[[:space:]]*DATABASE_URL=//; s/^["'\'']//; s/["'\'']$//' || true
  else
    echo ""
  fi
}

echo "DEV_DIR:  $DEV_DIR"
echo "PROD_DIR: $PROD_DIR"
echo

dev_url="$(extract_url "$DEV_DIR")"
prod_url="$(extract_url "$PROD_DIR")"

if [[ -z "$dev_url" ]]; then
  echo "DEV:  (нет DATABASE_URL в $DEV_DIR/.env или нет файла)"
else
  echo "DEV:  DATABASE_URL=$dev_url"
fi
if [[ -z "$prod_url" ]]; then
  echo "PROD: (нет DATABASE_URL в $PROD_DIR/.env или нет файла)"
else
  echo "PROD: DATABASE_URL=$prod_url"
fi
echo

if [[ -n "$dev_url" && -n "$prod_url" ]]; then
  if [[ "$dev_url" == "$prod_url" ]]; then
    echo "Итог: ⚠️  Совпадает — dev и prod указывают на ОДНУ И ТУ ЖЕ БД (проверьте, это ли задумано)."
  else
    echo "Итог: ✅ Различается — обычно это разные БД (подтвердите по смыслу URL: разные файлы/хосты/имена БД)."
  fi
fi

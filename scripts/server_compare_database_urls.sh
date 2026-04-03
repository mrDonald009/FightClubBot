#!/usr/bin/env bash
# Сравнить DATABASE_URL тестового и боевого деплоя на сервере.
# Использование:
#   bash scripts/server_compare_database_urls.sh
#   bash scripts/server_compare_database_urls.sh /home/cat/FightClubBot-dev /home/cat/FightClubBot
#
# В каждом каталоге ищет первое непустое DATABASE_URL в файлах (как типичный приоритет):
#   .env → .env.dev → .env.prod → .env.local
# Так покрывается случай systemd с EnvironmentFile=.../.env.dev при «пустом» .env.

set -euo pipefail

DEV_DIR="${1:-${FIGHTCLUB_DEV_DIR:-$HOME/FightClubBot-dev}}"
PROD_DIR="${2:-${FIGHTCLUB_PROD_DIR:-$HOME/FightClubBot}}"

ENV_CANDIDATES=(".env" ".env.dev" ".env.prod" ".env.local")

pick_database_url() {
  local dir="$1"
  local f path u
  for f in "${ENV_CANDIDATES[@]}"; do
    path="$dir/$f"
    [[ -f "$path" ]] || continue
    u=$(grep -E '^[[:space:]]*DATABASE_URL=' "$path" | head -1 | sed 's/^[[:space:]]*DATABASE_URL=//; s/^["'\'']//; s/["'\'']$//' || true)
    [[ -z "$u" ]] && continue
    printf '%s\t%s\n' "$f" "$u"
    return 0
  done
  return 1
}

echo "DEV_DIR:  $DEV_DIR"
echo "PROD_DIR: $PROD_DIR"
echo "(поиск DATABASE_URL: ${ENV_CANDIDATES[*]})"
echo

dev_file=""
dev_url=""
if IFS=$'\t' read -r dev_file dev_url < <(pick_database_url "$DEV_DIR"); then
  echo "DEV:  DATABASE_URL=$dev_url  (файл: $dev_file)"
else
  echo "DEV:  (нет DATABASE_URL ни в одном из: ${ENV_CANDIDATES[*]} в $DEV_DIR)"
fi

prod_file=""
prod_url=""
if IFS=$'\t' read -r prod_file prod_url < <(pick_database_url "$PROD_DIR"); then
  echo "PROD: DATABASE_URL=$prod_url  (файл: $prod_file)"
else
  echo "PROD: (нет DATABASE_URL ни в одном из: ${ENV_CANDIDATES[*]} в $PROD_DIR)"
fi
echo

if [[ -n "$dev_url" && -n "$prod_url" ]]; then
  if [[ "$dev_url" == "$prod_url" ]]; then
    echo "Итог: ⚠️  Строки DATABASE_URL совпадают."
    echo "      Для SQLite путь в URL часто относительный: физический файл зависит от WorkingDirectory процесса."
    echo "      Если каталоги деплоя разные — это обычно разные файлы БД; если один каталог — проверьте вручную."
  else
    echo "Итог: ✅ Строки различаются — как правило, разные БД (проверьте смысл URL)."
  fi
fi

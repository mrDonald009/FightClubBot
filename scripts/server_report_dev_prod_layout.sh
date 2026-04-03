#!/usr/bin/env bash
# Отчёт по структуре dev/prod на сервере (каталоги, .env, DATABASE_URL, файлы БД).
# Запуск на сервере:
#   bash scripts/server_report_dev_prod_layout.sh
# Свои пути:
#   FIGHTCLUB_DEV_DIR=/path/to/dev FIGHTCLUB_PROD_DIR=/path/to/prod bash scripts/server_report_dev_prod_layout.sh

set -euo pipefail

DEV="${FIGHTCLUB_DEV_DIR:-$HOME/FightClubBot-dev}"
PROD="${FIGHTCLUB_PROD_DIR:-$HOME/FightClubBot}"

ENV_CANDIDATES=(".env" ".env.dev" ".env.prod" ".env.local")

section() {
  printf "\n%s\n" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  printf "%s\n" "$1"
  printf "%s\n" "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

report_one() {
  local label="$1"
  local dir="$2"

  section "$label — $dir"

  if [[ ! -d "$dir" ]]; then
    echo "Каталог не найден."
    return 0
  fi

  echo "--- путь и Git ---"
  (cd "$dir" && pwd)
  (cd "$dir" && git rev-parse --show-toplevel 2>/dev/null || echo "(не git-репозиторий)")
  (cd "$dir" && git branch --show-current 2>/dev/null || true)
  (cd "$dir" && git rev-parse --short HEAD 2>/dev/null || true)
  echo "--- git remote (первые строки) ---"
  (cd "$dir" && git remote -v 2>/dev/null | head -4 || true)

  echo "--- файлы окружения в корне ---"
  ls -la "$dir"/.env "$dir"/.env.dev "$dir"/.env.prod 2>/dev/null || echo "(нет типичных .env*)"

  echo "--- DATABASE_URL (по файлам: ${ENV_CANDIDATES[*]}) ---"
  local f found=0
  for f in "${ENV_CANDIDATES[@]}"; do
    if [[ -f "$dir/$f" ]]; then
      if grep -qE '^[[:space:]]*DATABASE_URL=' "$dir/$f" 2>/dev/null; then
        echo "# из $f:"
        grep -E '^[[:space:]]*DATABASE_URL=' "$dir/$f" || true
        found=1
      fi
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "(ни в одном из перечисленных файлов нет DATABASE_URL)"
  fi

  echo "--- каталог database/ ---"
  if [[ -d "$dir/database" ]]; then
    ls -la "$dir/database/" 2>/dev/null || true
    ls -la "$dir/database"/*.db 2>/dev/null || echo "(нет *.db)"
  else
    echo "(нет каталога database/)"
  fi

  echo "--- global_freezes (если есть SQLite и sqlite3) ---"
  local dbfile=""
  local candidates
  if [[ "$dir" == *"FightClubBot-dev"* ]] || [[ "$dir" == *"-dev" ]]; then
    candidates=("$dir/database/club_dev.db" "$dir/database/club.db")
  else
    candidates=("$dir/database/club.db" "$dir/database/club_dev.db")
  fi
  local cand
  for cand in "${candidates[@]}"; do
    if [[ -f "$cand" ]]; then
      dbfile="$cand"
      break
    fi
  done
  if [[ -n "$dbfile" ]] && command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$dbfile" "SELECT COUNT(*) AS cnt FROM global_freezes;" 2>/dev/null \
      && sqlite3 "$dbfile" "SELECT id, title, is_active, start_date, end_date FROM global_freezes ORDER BY id;" 2>/dev/null \
      || echo "(таблица global_freezes недоступна)"
  elif [[ -n "$dbfile" ]]; then
    echo "sqlite3 не в PATH; файл БД: $dbfile"
  else
    echo "(не найден database/club_dev.db или database/club.db)"
  fi
}

section "FightClubBot — отчёт dev / prod на этом хосте"
echo "Время: $(date -Iseconds 2>/dev/null || date)"
echo "Хост: $(hostname 2>/dev/null || echo '?')"
echo ""
echo "Переопределение путей: FIGHTCLUB_DEV_DIR, FIGHTCLUB_PROD_DIR"

report_one "DEV (тестовый)" "$DEV"
report_one "PROD (боевой)" "$PROD"

section "systemd — что выполнить вручную"
echo "Имена юнитов у вас могут отличаться. Примеры:"
echo "  systemctl list-units --type=service --all | grep -i fight"
echo "  systemctl cat <имя>.service | grep -iE 'WorkingDirectory|EnvironmentFile|ExecStart|User='"
echo ""
echo "Готово."

#!/usr/bin/env bash
# Сравнение списка спортсменов тренера (по created_by + вид спорта) между текущей БД и бэкапом.
# Пути по умолчанию — эталон VPS (cat@158799); при необходимости переопредели переменные.
#
# Пример (dev, бэкап с конца 03.04.2026):
#   bash scripts/compare_coach_athletes_snapshot.sh
#
# Пример (prod):
#   DB_NOW=/home/cat/FightClubBot/database/club.db \
#   DB_BAK=/home/cat/FightClubBot/database/club.db.bak-202604032205 \
#   bash scripts/compare_coach_athletes_snapshot.sh

set -euo pipefail

TG="${TG:-416035374}"
SPORT="${SPORT:-Тайский Бокс}"
DB_NOW="${DB_NOW:-/home/cat/FightClubBot-dev/database/club_dev.db}"
DB_BAK="${DB_BAK:-/home/cat/FightClubBot-dev/database/club_dev.db.bak-202604032207}"

for f in "$DB_NOW" "$DB_BAK"; do
  if [[ ! -f "$f" ]]; then
    echo "Нет файла: $f" >&2
    exit 1
  fi
done

CID_NOW=$(sqlite3 "$DB_NOW" "SELECT id FROM coaches WHERE telegram_id=$TG;")
CID_BAK=$(sqlite3 "$DB_BAK" "SELECT id FROM coaches WHERE telegram_id=$TG;")

echo "=== Параметры ==="
echo "TG=$TG  sport_type='$SPORT'"
echo "DB_NOW=$DB_NOW"
echo "DB_BAK=$DB_BAK"
echo "coaches.id: сейчас=$CID_NOW  в бэкапе=$CID_BAK"
echo

if [[ -z "$CID_NOW" || -z "$CID_BAK" ]]; then
  echo "Ошибка: тренер с telegram_id=$TG не найден в одной из БД." >&2
  exit 1
fi

echo "=== COUNT (created_by + sport_type) ==="
echo -n "сейчас: "
sqlite3 "$DB_NOW" "SELECT COUNT(*) FROM athletes WHERE created_by=$CID_NOW AND sport_type='$SPORT';"
echo -n "бэкап:  "
sqlite3 "$DB_BAK" "SELECT COUNT(*) FROM athletes WHERE created_by=$CID_BAK AND sport_type='$SPORT';"
echo

echo "=== id|full_name|created_by — бэкап ==="
sqlite3 -header -column "$DB_BAK" \
  "SELECT id, full_name, created_by FROM athletes WHERE created_by=$CID_BAK AND sport_type='$SPORT' ORDER BY id;"
echo

echo "=== id|full_name|created_by — сейчас ==="
sqlite3 -header -column "$DB_NOW" \
  "SELECT id, full_name, created_by FROM athletes WHERE created_by=$CID_NOW AND sport_type='$SPORT' ORDER BY id;"

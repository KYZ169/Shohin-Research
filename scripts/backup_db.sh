#!/bin/bash
# PostgreSQL(resale_radar)の自動バックアップ。
#
# docker composeの db サービスコンテナ内でpg_dumpを実行する(ホスト側libpqと
# postgres:16-alpineのバージョン不一致を避けるため)。dbコンテナが起動していない
# 場合は空/壊れたバックアップを残さず即座に失敗する。
#
# 使い方(手動実行):
#   scripts/backup_db.sh
#
# cron登録例(毎日4:10に実行、Aqualium側のbackup_all.shが4:00のため10分ずらす):
#   10 4 * * * /home/user1/Shohin-Research/scripts/backup_db.sh >> /home/user1/backups/shohin-research/backup.log 2>&1
#
# 復元手順:
#   gunzip -c /home/user1/backups/shohin-research/resale_radar_YYYYmmdd_HHMMSS.sql.gz \
#     | docker compose exec -T db psql -U resale_radar resale_radar

set -euo pipefail

PROJECT_DIR="/home/user1/Shohin-Research"
BACKUP_DIR="/home/user1/backups/shohin-research"
RETENTION_DAYS=14
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DEST="${BACKUP_DIR}/resale_radar_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

if ! docker compose exec -T db pg_isready -U resale_radar >/dev/null 2>&1; then
    echo "[backup_db] $(date -Iseconds) ERROR: dbコンテナが起動していない、またはpg_isready失敗のため中止します" >&2
    exit 1
fi

TMP_DEST="${DEST}.tmp"
docker compose exec -T db pg_dump -U resale_radar resale_radar | gzip > "$TMP_DEST"

# ダンプが空/壊れていないことを確認してから本配置にリネームする
# (途中で失敗した中途半端なファイルが正常なバックアップとして扱われるのを防ぐ)。
if [ ! -s "$TMP_DEST" ] || ! gzip -t "$TMP_DEST" 2>/dev/null; then
    echo "[backup_db] $(date -Iseconds) ERROR: pg_dumpの出力が空、または壊れています" >&2
    rm -f "$TMP_DEST"
    exit 1
fi
mv "$TMP_DEST" "$DEST"

echo "[backup_db] $(date -Iseconds) OK: ${DEST} ($(du -h "$DEST" | cut -f1))"

# 保持期間(14日)を超えた古いバックアップを削除
find "$BACKUP_DIR" -maxdepth 1 -name 'resale_radar_*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete

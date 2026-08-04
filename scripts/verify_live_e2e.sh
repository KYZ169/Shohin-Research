#!/usr/bin/env bash
# 実データE2E検証 一括実行スクリプト(タスク19)。
#
# tmuxセッション内で、以下を1コマンドで行う:
#   1. docker composeでdb/redisを起動
#   2. alembic upgrade head でスキーマを最新化(初回のみ実質的に意味がある。冪等)
#   3. docker composeでworker/beatをバックグラウンド起動(ログはdocker compose logsで
#      後からまとめて見られるほか、このスクリプト自身もファイルへ保存する)
#   4. Celeryの定期タスク(run_ichiban_kuji_collector)を1回だけ手動triggerし、
#      workerが実際にタスクを拾って実行できることを確認する
#      (fetch→parse→ログ出力のみ。DB反映やDiscord通知はしない設計。
#      app/scheduler/tasks.pyのモジュールdocstring参照)
#   5. scripts/verify_live_e2e.py で「収集→DB反映→商品照合→Profit Engine→
#      Opportunity生成→通知判定→Discord送信」の一連を検証する
#
# 使い方(tmux起動後、このリポジトリのルートで):
#   bash scripts/verify_live_e2e.sh
#
# worker/beatはこのスクリプト終了後も動き続ける(バックグラウンドのdocker
# コンテナのため)。停止方法はdocs/live_verification_guide.md「クリーンアップ手順」を参照。

set -euo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="./verify_logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
SUMMARY_LOG="$LOG_DIR/summary_${TS}.log"

# ローカルのvenvで直接実行する場合(docker composeのdb/redisをホスト側ポートで叩く)、
# api/worker/beatコンテナ用の.envはDATABASE_URL/REDIS_URLがdocker内部ホスト名
# (db/redis)になっているため、host側で動くこのスクリプト・verify_live_e2e.pyからは
# localhost:<ホスト公開ポート>で上書きする。docker-compose.ymlのdbサービスは、
# 本番ConoHa VPS上で別プロジェクト(aquarium系Bot群)のネイティブPostgreSQLが
# 既にホストの5432番を使用しているため、ホスト側ポートを55432にずらしてある
# (docker-compose.ymlのdbサービスのコメント参照。コンテナ間通信への影響は無い)。
export DATABASE_URL="postgresql+psycopg2://resale_radar:resale_radar@localhost:55432/resale_radar"
export REDIS_URL="redis://localhost:6379/0"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$SUMMARY_LOG"; }

log "===================================================================="
log " 実データE2E検証を開始します(ログ一式: $LOG_DIR/*_${TS}.*)"
log "===================================================================="

log "[1/5] docker compose で db/redis を起動..."
docker compose up -d db redis 2>&1 | tee -a "$LOG_DIR/docker_up_${TS}.log"

log "  db/redisのヘルスチェック待機..."
for _ in $(seq 1 30); do
    db_status="$(docker compose ps db --format '{{.Health}}' 2>/dev/null || true)"
    redis_status="$(docker compose ps redis --format '{{.Health}}' 2>/dev/null || true)"
    if [ "$db_status" = "healthy" ] && [ "$redis_status" = "healthy" ]; then
        log "  db/redis: healthy"
        break
    fi
    sleep 2
done
if [ "$db_status" != "healthy" ] || [ "$redis_status" != "healthy" ]; then
    log "  [NG] db/redisがhealthyになりませんでした。'docker compose logs db redis' を確認してください。"
    exit 1
fi

log "[2/5] alembic upgrade head でスキーマを最新化..."
venv/bin/python3 -m alembic upgrade head 2>&1 | tee -a "$LOG_DIR/alembic_${TS}.log"

log "[3/5] docker compose で worker/beat をバックグラウンド起動..."
docker compose up -d --build worker beat 2>&1 | tee -a "$LOG_DIR/docker_up_${TS}.log"
log "  workerの起動待機(ready ログを確認)..."
for _ in $(seq 1 30); do
    if docker compose logs worker 2>/dev/null | grep -q "celery@.* ready\."; then
        log "  worker: ready"
        break
    fi
    sleep 2
done
docker compose logs worker 2>&1 | tail -n 30 >> "$LOG_DIR/worker_${TS}.log"
docker compose logs beat 2>&1 | tail -n 30 >> "$LOG_DIR/beat_${TS}.log"

log "[4/5] Celeryの定期タスク(run_ichiban_kuji_collector)を1回だけ手動triggerして、"
log "      workerがブローカー経由でタスクを実際に実行できることを確認..."
venv/bin/python3 -c "
from app.scheduler.tasks import run_ichiban_kuji_collector
r = run_ichiban_kuji_collector.delay()
result = r.get(timeout=60)
print('  タスク結果:', result)
" 2>&1 | tee -a "$SUMMARY_LOG"
log "  (このタスクはfetch→parse→ログ出力のみを行う設計です。DB反映やDiscord通知はしません。"
log "   収集→通知までの一連の動作は次のステップで別途確認します)"

log "[5/5] 収集→DB反映→商品照合→Profit Engine→Opportunity→Discord通知のE2E検証..."
venv/bin/python3 scripts/verify_live_e2e.py 2>&1 | tee -a "$LOG_DIR/e2e_${TS}.log" | tee -a "$SUMMARY_LOG"
E2E_EXIT_CODE=${PIPESTATUS[0]}

log "===================================================================="
if [ "$E2E_EXIT_CODE" -eq 0 ]; then
    log " 検証スクリプトは正常終了しました(exit code 0)。"
else
    log " 検証スクリプトはエラーで終了しました(exit code ${E2E_EXIT_CODE})。"
    log " ログ: $LOG_DIR/e2e_${TS}.log / $SUMMARY_LOG"
fi
log "===================================================================="
log ""
log " Discordのチャンネルを確認してください。"
log ""
log " worker/beatはバックグラウンドで動き続けています。停止方法は"
log " docs/live_verification_guide.md の「クリーンアップ手順」を参照してください。"

exit "$E2E_EXIT_CODE"

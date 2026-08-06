"""Celery Beatでの定期実行結線(CLAUDE.mdタスク12)。

CLAUDE.md 8.5.4: 一番くじは6時間に1回。
CLAUDE.md 1.3: 駿河屋は`purchase_hendou=価格上昇中`で絞った差分取得を1日1〜2回。
CLAUDE.md 13.3節(タスク21〜22): ポケモンセンターオンラインは一番くじと同じ6時間に1回。
仕入れ側(SourceCollector)としての性質が一番くじと同じであり、初期値も揃える
(要検証: 巡回頻度を裏付ける実データ根拠は無く、一番くじにならった暫定値)。
プレミアムバンダイCollector(タスク5)はユーザー判断で保留中のため、Beatスケジュールにも
含めない(実装され次第ここに追加する)。
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "resale_radar",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.scheduler.tasks"],
)
celery_app.conf.timezone = "Asia/Tokyo"
celery_app.conf.enable_utc = False

celery_app.conf.beat_schedule = {
    "ichiban-kuji-every-6-hours": {
        "task": "app.scheduler.tasks.run_ichiban_kuji_collector",
        "schedule": 6 * 60 * 60,
    },
    "suruga-ya-price-rising-scan-twice-daily": {
        "task": "app.scheduler.tasks.run_suruga_ya_price_rising_scan",
        "schedule": 12 * 60 * 60,
    },
    "pokemon-center-online-every-6-hours": {
        "task": "app.scheduler.tasks.run_pokemon_center_collector",
        "schedule": 6 * 60 * 60,
    },
}

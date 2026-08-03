"""Celery Beatから定期実行されるタスク定義(CLAUDE.mdタスク12)。

【スコープに関する注記】
ここではSource/Market Collectorの定期実行そのもの(fetch→parse→normalize)のみを
配線する。収集結果(NormalizedItem/MarketObservation)をrelease_events/
profit_snapshots/opportunitiesへ実際に反映するパイプライン(Product Matcherを
介したDB書き込み、Opportunity生成、Discord通知トリガー)はCLAUDE.md Phase 0の
タスク一覧に明示的な項目が無く、これ単体でも相応の規模になるためスコープ外とする
(CLAUDE.md 0.5「タスクの粒度・分割方法は自分で判断してよい」に基づく判断)。
本タスクでは収集結果を構造化ログに出力するところまでを「定期実行結線」の完了条件とする。
"""

import asyncio
import logging

from app.collectors.base import CollectorRunResult, FetchError, ParseError
from app.collectors.markets.suruga_ya import SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.scheduler.celery_app import celery_app

logger = logging.getLogger(__name__)

# CLAUDE.md 1.3: 「巡回時はpurchase_hendou=価格上昇中で絞った差分取得を定期実行すると、
# 通知価値の高い案件を効率的に拾える」との推奨はあるが、具体的にどのキーワードで
# 巡回すべきかは明記されていない。技術分析レポート4.1のMVP最優先ジャンル
# (ポケモンカード/一番くじ)を暫定の巡回キーワードとして採用する(要検証)。
SURUGA_YA_SCAN_KEYWORDS = ["一番くじ", "ポケモンカード"]
SURUGA_YA_PRICE_RISING_RESTRICT = "purchase_hendou=価格上昇中"


@celery_app.task(name="app.scheduler.tasks.run_ichiban_kuji_collector")
def run_ichiban_kuji_collector() -> dict:
    """CLAUDE.md 8.5.4: 6時間に1回、1kuji.comのPICK UP ITEMを巡回する。"""
    collector = IchibanKujiCollector()
    result: CollectorRunResult = asyncio.run(collector.run())

    logger.info(
        "ichiban_kuji collector run finished: health=%s success=%d error=%d errors=%s",
        result.health.value,
        result.success_count,
        result.error_count,
        result.errors,
    )
    return {
        "source_name": result.source_name,
        "health": result.health.value,
        "success_count": result.success_count,
        "error_count": result.error_count,
    }


@celery_app.task(name="app.scheduler.tasks.run_suruga_ya_price_rising_scan")
def run_suruga_ya_price_rising_scan() -> dict:
    """CLAUDE.md 1.3: 価格上昇中フィルタでの差分取得を1日1〜2回実行する。

    MarketCollectorはSourceCollectorと異なりrun()を持たない(技術分析10章の設計どおり
    search()/parse_observations()のみ)ため、このタスク内でオーケストレーションする。
    """
    collector = SurugaYaCollector()
    keyword_results: dict[str, int] = {}
    error_count = 0

    for keyword in SURUGA_YA_SCAN_KEYWORDS:
        try:
            raw = asyncio.run(
                collector.search(keyword, identifiers={}, restrict=[SURUGA_YA_PRICE_RISING_RESTRICT])
            )
            observations = collector.parse_observations(raw)
            keyword_results[keyword] = len(observations)
            logger.info(
                "suruga_ya price-rising scan: keyword=%s observations=%d", keyword, len(observations)
            )
        except (FetchError, ParseError) as exc:
            error_count += 1
            logger.warning("suruga_ya price-rising scan failed for keyword=%s: %s", keyword, exc)

    return {"keyword_results": keyword_results, "error_count": error_count}

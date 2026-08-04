"""Celery Beatから定期実行されるタスク定義(CLAUDE.mdタスク12)。

【スコープに関する注記】
ここではSource/Market Collectorの定期実行そのもの(fetch→parse→normalize)のみを
配線する。収集結果(NormalizedItem/MarketObservation)をrelease_events/
profit_snapshots/opportunitiesへ実際に反映するパイプライン(Product Matcherを
介したDB書き込み、Opportunity生成、Discord通知トリガー)はCLAUDE.md Phase 0の
タスク一覧に明示的な項目が無く、これ単体でも相応の規模になるためスコープ外とする
(CLAUDE.md 0.5「タスクの粒度・分割方法は自分で判断してよい」に基づく判断)。
本タスクでは収集結果を構造化ログに出力するところまでを「定期実行結線」の完了条件とする。

【手動検証専用タスクについて(タスク19)】
run_live_e2e_verification()は上記のスコープ外判断とは別に、「収集→DB反映→商品照合→
Profit Engine→Opportunity生成→通知判定」の一連が実際に動くことをConoHa VPS上で
人力検証するための専用タスク。beat_scheduleには登録せず、
scripts/verify_live_e2e.pyからの手動triggerのみを想定する(実運用の自動パイプライン
ではない。上記のスコープ外判断そのものは変わっていない)。
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

import redis as redis_module

from app.collectors.base import CollectorRunResult, FetchError, ParseError
from app.collectors.markets.base import MarketDataType, MarketObservation
from app.collectors.markets.suruga_ya import SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.config import settings
from app.core.time import JST
from app.db.models import Product, Shop, Source
from app.db.session import SessionLocal
from app.pipeline.ingest import ingest_normalized_item
from app.pipeline.market_matching import match_observation_to_product
from app.pipeline.notify import evaluate_notification
from app.pipeline.opportunity_pipeline import build_and_score_opportunity
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


# タスク19: scripts/verify_live_e2e.py専用。実データでのbandaispirits.co.jp検証
# (CLAUDE.md 1.6節・タスク18)で既に動作確認済みの安定したprd_idをデフォルトにする。
VERIFICATION_DEFAULT_PRD_ID = "kimetsu29"
VERIFICATION_SOURCE_COLLECTOR_KEY = "ichiban_kuji_verify_manual"
# 相場側の合成価格に上乗せする金額の基準値。確実にプラスの利益が出るようにするための
# プレースホルダーであり、実在の買取相場ではない(タスク本体のdocstring参照)。
# 実行のたびに端数をミリ秒ベースの値で変えているのは、dedupe判定
# (app/notification/dedupe.py)が「前回と同じdisplayed_profitなら送信しない」設計のため、
# 検証スクリプトを複数回実行しても必ず新規/更新扱いになるようにするため
# (秒(0〜59)単位だと同じ分内の連続実行で衝突することを自己検証時に実際に確認したため、
# ミリ秒単位まで使う)。
VERIFICATION_SYNTHETIC_MARKUP_BASE = Decimal("3000")


@celery_app.task(name="app.scheduler.tasks.run_live_e2e_verification")
def run_live_e2e_verification(bandaispirits_prd_id: str = VERIFICATION_DEFAULT_PRD_ID) -> dict:
    """scripts/verify_live_e2e.py専用の手動検証タスク(タスク19)。

    実際にbandaispirits.co.jpの商品詳細ページをfetchし、ingest→商品照合→
    Profit Engine→Opportunity生成→通知判定(evaluate_notification)までを実行する。

    Discordへの実送信(discord.Clientによるゲートウェイ接続)はこのタスクの中では
    行わない。Celeryタスクは同期実行モデルが基本であり、discord.pyの非同期
    ゲートウェイ接続と相性が悪いため、意図的に分離した。should_send=Trueの場合は
    embed(dict、JSON化可能)を返すところまでとし、実送信は呼び出し側
    (scripts/verify_live_e2e.py)が別プロセス・別のasyncioイベントループで行う。

    【相場側データについて】仕入れ側(bandaispirits.co.jpの商品詳細)は実際にfetchした
    実データを使うが、相場側(買取/転売価格)は「同じ商品を扱う実在のリストを自動的に
    見つける仕組み」がこのCollector群には無い(1kuji.com/bandaispirits.co.jpと
    suruga-ya.jp等の対応関係は未確立、CLAUDE.md参照)ため、確実に一連の動作を
    最後まで確認できるよう合成のMarketObservationを使う
    (extra["verification_synthetic"]=Trueで明示)。Profit Engine/Opportunity Scorerの
    計算ロジック自体は本物であり、入力の一部がプレースホルダーというだけである。
    """
    stages: dict = {"stage": "fetch_bandaispirits_detail"}

    try:
        collector = IchibanKujiCollector()
        parsed_item = asyncio.run(collector.fetch_bandaispirits_detail(bandaispirits_prd_id))
    except (FetchError, ParseError) as exc:
        logger.error("[verify_e2e] bandaispirits fetch/parse failed: %s", exc)
        stages.update(ok=False, error=str(exc))
        return stages

    normalized_item = collector.normalize([parsed_item])[0]

    if normalized_item.parsed.price is None or not normalized_item.product_name:
        stages.update(
            ok=False,
            error=f"価格または商品名が抽出できませんでした(product_name={normalized_item.product_name!r})",
        )
        return stages

    stages["fetch_bandaispirits_detail"] = {
        "ok": True,
        "product_name": normalized_item.product_name,
        "price": str(normalized_item.parsed.price),
        "url": normalized_item.parsed.product_url,
    }

    session = SessionLocal()
    try:
        stages["stage"] = "ingest"
        source = session.query(Source).filter_by(collector_key=VERIFICATION_SOURCE_COLLECTOR_KEY).one_or_none()
        if source is None:
            source = Source(
                name="[検証専用] 一番くじ公式(bandaispirits.co.jp)",
                base_url="https://www.bandaispirits.co.jp",
                collector_key=VERIFICATION_SOURCE_COLLECTOR_KEY,
            )
            session.add(source)
            session.flush()

        release_event, ingest_match_status = ingest_normalized_item(session, source, normalized_item)
        session.flush()
        product = session.get(Product, release_event.product_id)
        shop = session.get(Shop, release_event.shop_id)

        stages["ingest"] = {
            "ok": True,
            "release_event_id": str(release_event.id),
            "product_id": str(product.id),
            "match_status": ingest_match_status.value,
        }

        stages["stage"] = "synthetic_market_observation"
        millis_component = int(datetime.now(tz=JST).timestamp() * 1000) % 100000
        markup = VERIFICATION_SYNTHETIC_MARKUP_BASE + Decimal(millis_component)
        synthetic_amount = release_event.price + markup
        observation = MarketObservation(
            product_ref=normalized_item.product_name,
            data_type=MarketDataType.BUYBACK_PRICE,
            amount=synthetic_amount,
            count=None,
            observed_at=datetime.now(tz=JST),
            source_url=normalized_item.parsed.product_url,
            confidence="B",
            extra={"title": normalized_item.product_name, "verification_synthetic": True},
        )
        stages["synthetic_market_observation"] = {
            "ok": True,
            "amount": str(synthetic_amount),
            "note": "実在の買取価格ではなく、通知まで届くことを保証するための合成データ",
        }

        stages["stage"] = "match"
        match = match_observation_to_product(session, observation)
        if match is None:
            stages["match"] = {"ok": False, "error": "match_observation_to_product()がNoneを返しました(想定外)"}
            session.rollback()
            return stages
        matched_product, market_match_status = match
        stages["match"] = {"ok": True, "match_status": market_match_status.value}

        stages["stage"] = "opportunity"
        build_result = build_and_score_opportunity(
            session,
            release_event=release_event,
            product=product,
            observation=observation,
            match_status=market_match_status,
            channel_name="verification_synthetic",
        )
        session.flush()
        if build_result is None:
            stages["opportunity"] = {"ok": False, "error": "build_and_score_opportunity()がNoneを返しました(価格未確定)"}
            session.rollback()
            return stages
        stages["opportunity"] = {
            "ok": True,
            "displayed_profit": str(build_result.standard_displayed_profit),
            "opportunity_id": str(build_result.opportunity.id),
        }

        stages["stage"] = "notify_decision"
        redis_client = redis_module.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            decision = evaluate_notification(
                redis_client,
                release_event=release_event,
                shop=shop,
                product=product,
                source=source,
                observation=observation,
                standard_displayed_profit=build_result.standard_displayed_profit,
                standard_excluded_cost_items=build_result.standard_excluded_cost_items,
                match_status=market_match_status,
                target_prefectures=set(),
            )
        finally:
            redis_client.close()

        session.commit()

        stages["notify_decision"] = {
            "ok": True,
            "should_send": decision.should_send,
            "dedupe_decision": decision.dedupe_result.decision.value if decision.dedupe_result else None,
            "excluded_reason": decision.excluded_reason,
        }
        stages["stage"] = "done"
        stages["ok"] = True
        stages["should_send"] = decision.should_send
        stages["embed"] = decision.embed
        return stages
    except Exception as exc:
        session.rollback()
        logger.exception("[verify_e2e] failed at stage=%s", stages.get("stage"))
        stages["ok"] = False
        stages["error"] = f"{type(exc).__name__}: {exc}"
        return stages
    finally:
        session.close()

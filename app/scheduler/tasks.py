"""Celery Beatから定期実行されるタスク定義(CLAUDE.mdタスク12)。

【スコープに関する注記(2026-08-06、タスク20で更新)】
当初(タスク12)はSource/Market Collectorの定期実行そのもの(fetch→parse→normalize)の
配線のみをスコープとし、収集結果をDBへ反映するパイプラインは別タスク扱いとしていた。
その結果、run_ichiban_kuji_collector/run_suruga_ya_price_rising_scanのいずれも
collector_runsテーブルへのログ記録のみで、products/release_events/opportunitiesへの
書き込みを一切行わない状態が続いていた(発見した新商品がDiscord通知に構造的に
一切繋がらないという重大な欠落。2026-08-06の本番実機検証で発覚、CLAUDE.md 3節・
11節参照)。本タスク(タスク20)でこれを解消した:
- run_ichiban_kuji_collector: ingest_normalized_item()(タスク13)を呼び出し、
  収集した商品を実際にproducts/release_eventsへ反映するようにした。
- run_suruga_ya_price_rising_scan: match_observation_to_product()(タスク13)で
  観測した買取価格を既存Productと照合し、一致すればbuild_and_score_opportunity()/
  evaluate_notification()(いずれもタスク13で実装済み、変更無し)でOpportunity生成・
  通知判定まで行うようにした。
一番くじ側(仕入れ)は相場データを持たないため、Opportunity生成・通知判定は相場側
(suruga_ya)がマッチした時点で行う設計とした(そこが自然な結合点であり、
一番くじCollector自身がsuruga_yaを検索しにいく必要が無い)。

【ポケモンセンターオンラインの追加(2026-08-06、タスク22・段階2)】
run_pokemon_center_collector()を追加した。一番くじと同じ仕入れ側(SourceCollector)の
性質のため、設計は完全にrun_ichiban_kuji_collector()を踏襲する(ingest_normalized_item()
のみ、Opportunity生成・通知判定はsuruga_ya側の自然な結合点に委ねる)。相違点は
収集の入口がcollector.target_urlsの単層ループではなく、discover_new_products()
(app/collectors/sources/pokemon_center_online.py、一覧ページ→商品コード抽出→
個別ページ275件のfetch_product()という2段階の収集)である点のみ。beat_scheduleにも
一番くじと同じ6時間間隔で登録した(app/scheduler/celery_app.py)。

【手動検証専用タスクについて(タスク19)】
run_live_e2e_verification()はタスク20より前から、「収集→DB反映→商品照合→
Profit Engine→Opportunity生成→通知判定」の一連が実際に動くことをConoHa VPS上で
人力検証するための専用タスクとして存在していた(合成のMarketObservationを使うため
実データのみのタスク20とは別物として維持している)。beat_scheduleには登録せず、
scripts/verify_live_e2e.pyからの手動triggerのみを想定する。

【Discord送信について(タスク19・20共通の設計方針)】
いずれのタスクもDiscordへの実送信(discord.Clientによるゲートウェイ接続)は
タスクの中では行わない。Celeryタスクは同期実行モデルが基本であり、discord.pyの
非同期ゲートウェイ接続と相性が悪いため、意図的に分離している。should_send=Trueの
ものはembed(dict、JSON化可能)を返り値に含めるところまでとし、実送信は呼び出し側
(常時起動Bot等、discord.Clientを持つ別プロセス)が担当する。

【2026-08-06・緊急対応(タスク23)で解消】上記の「実送信を自動的に拾って送る仕組み」
自体がタスク20〜22の時点で存在せず、should_send=Trueの結果はタスクの戻り値(Celeryの
結果バックエンド、TTLで消える)に含まれるだけで、誰にも消費されずに失われていた。
これは収集→通知という当初からの目的そのものに関わる欠落だったため緊急対応した。
`_match_and_score_observation()`がshould_send=Trueと判定した時点で
`pending_notifications`テーブル(app/db/models/pending_notification.py)へ1行永続化し、
常時起動Bot(app/bot/main.py)が15分毎(`app/notification/dispatcher.py`)にこれを
ポーリングして実際に送信する。詳細はCLAUDE.md 15節参照。
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

import redis as redis_module
from sqlalchemy.orm import Session

from app.collectors.base import CollectorHealth, FetchError, ParseError
from app.collectors.markets.base import MarketDataType, MarketObservation
from app.collectors.markets.suruga_ya import SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.collectors.sources.pokemon_center_online import PokemonCenterOnlineCollector
from app.config import settings
from app.core.time import JST
from app.db.models import ManualReviewTask, Opportunity, PendingNotification, Product, ReleaseEvent, Shop, Source
from app.db.session import SessionLocal
from app.domain.enums import ManualReviewTaskStatus, MatchStatus
from app.pipeline.ingest import ingest_normalized_item
from app.pipeline.market_matching import match_observation_to_product
from app.pipeline.notify import evaluate_notification
from app.pipeline.opportunity_pipeline import build_and_score_opportunity
from app.scheduler.celery_app import celery_app
from app.scheduler.run_log import record_collector_run

logger = logging.getLogger(__name__)


def _pending_manual_review_task_id(session: Session, opportunity: Opportunity) -> str | None:
    """Discordの「照合を確定する」/「別商品として分離」ボタン(app/notification/
    interaction_view.py)の表示条件である「match_status==NEEDS_REVIEWかつ該当pending
    タスクが実在する」を判定し、該当すればmanual_review_task_idを返す
    (2026-08-05に各所で個別実装していたものをタスク20で共通ヘルパーとして抽出)。
    """
    if opportunity.match_status != MatchStatus.NEEDS_REVIEW:
        return None
    pending_task = (
        session.query(ManualReviewTask)
        .filter_by(candidate_product_id=opportunity.product_id, status=ManualReviewTaskStatus.PENDING)
        .order_by(ManualReviewTask.created_at.desc())
        .first()
    )
    return str(pending_task.id) if pending_task is not None else None

# CLAUDE.md 1.3: 「巡回時はpurchase_hendou=価格上昇中で絞った差分取得を定期実行すると、
# 通知価値の高い案件を効率的に拾える」との推奨はあるが、具体的にどのキーワードで
# 巡回すべきかは明記されていない。技術分析レポート4.1のMVP最優先ジャンル
# (ポケモンカード/一番くじ)を暫定の巡回キーワードとして採用する(要検証)。
SURUGA_YA_SCAN_KEYWORDS = ["一番くじ", "ポケモンカード"]
SURUGA_YA_PRICE_RISING_RESTRICT = "purchase_hendou=価格上昇中"


ICHIBAN_KUJI_TASK_NAME = "app.scheduler.tasks.run_ichiban_kuji_collector"


ICHIBAN_KUJI_SOURCE_COLLECTOR_KEY = "ichiban_kuji"


@celery_app.task(name=ICHIBAN_KUJI_TASK_NAME)
def run_ichiban_kuji_collector() -> dict:
    """CLAUDE.md 8.5.4: 6時間に1回、1kuji.com/on-line.1kuji.comを巡回する。

    2026-08-06(タスク20): 従来はcollector.run()による収集ログ記録のみで、
    products/release_eventsへのDB反映を一切行っていなかった(モジュールdocstring
    「スコープに関する注記」参照)。collector.run()は各URLをfetchしたNormalizedItemを
    呼び出し側へ返さない設計のため、ここではrun()を使わず同等のfetch→parse→normalize→
    validateループを直接書き、ingest_normalized_item()(タスク13、変更無し)を
    呼び出して実際にDBへ反映するようにした。
    このCollector自身は相場側(買取価格等)のデータを持たないため、Opportunity生成・
    通知判定はここでは行わない。相場側のrun_suruga_ya_price_rising_scanが
    match_observation_to_product()で本タスクにより永続化されたProductと
    突き合わせた時点で行う(自然な結合点、モジュールdocstring参照)。
    """
    started_at = datetime.now(tz=JST)
    collector = IchibanKujiCollector()
    errors: list[str] = []
    normalized_items = []

    for index, url in enumerate(collector.target_urls):
        if index > 0:
            asyncio.run(asyncio.sleep(collector.rate_limit()))
        try:
            raw = asyncio.run(collector.fetch(url))
            parsed_items = collector.parse(raw)
        except (FetchError, ParseError) as exc:
            errors.append(f"fetch/parse failed for {url}: {exc}")
            continue
        normalized_items.extend(collector.normalize(parsed_items))

    success_count = 0
    ingested_count = 0
    # 取得できたアイテムが1件も無い場合(全URLのfetch/parseが失敗)は、DBセッションを
    # 開かずに終了する。record_collector_run()のみがDBへ触れる既存の挙動を維持し
    # (このタスクの単体テストがDB接続無しで完結できることの前提でもある)、
    # 無意味なSource行の作成も避ける。
    if normalized_items:
        session = SessionLocal()
        try:
            source = (
                session.query(Source).filter_by(collector_key=ICHIBAN_KUJI_SOURCE_COLLECTOR_KEY).one_or_none()
            )
            if source is None:
                source = Source(
                    name="一番くじ公式",
                    base_url="https://1kuji.com",
                    collector_key=ICHIBAN_KUJI_SOURCE_COLLECTOR_KEY,
                )
                session.add(source)
                session.flush()

            for item in normalized_items:
                field_errors = collector.validate(item)
                if field_errors:
                    errors.append(f"validation failed for '{item.product_name}': {'; '.join(field_errors)}")
                    continue
                success_count += 1
                ingest_normalized_item(session, source, item)
                ingested_count += 1

            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("[ichiban_kuji] DB ingest failed")
            errors.append(f"ingest failed: {type(exc).__name__}: {exc}")
            # このバッチ全体がロールバックされたため、ingest件数はゼロ扱いにする。
            ingested_count = 0
        finally:
            session.close()

    error_count = len(errors)
    if error_count == 0:
        health = CollectorHealth.OK
    elif success_count > 0:
        health = CollectorHealth.DEGRADED
    else:
        health = CollectorHealth.FAILING

    logger.info(
        "ichiban_kuji collector run finished: health=%s success=%d error=%d ingested=%d errors=%s",
        health.value,
        success_count,
        error_count,
        ingested_count,
        errors,
    )
    # OK/DEGRADED(一部欠落はあっても実行自体は完走)はsuccess、FAILING/DISABLEDはfailure扱い。
    status = "success" if health in (CollectorHealth.OK, CollectorHealth.DEGRADED) else "failure"
    record_collector_run(
        ICHIBAN_KUJI_TASK_NAME,
        started_at,
        status=status,
        summary=f"health={health.value} success={success_count} error={error_count} ingested={ingested_count}",
        error_message="; ".join(errors) if status == "failure" and errors else None,
    )
    return {
        "source_name": collector.source_name,
        "health": health.value,
        "success_count": success_count,
        "error_count": error_count,
        "ingested_count": ingested_count,
    }


POKEMON_CENTER_TASK_NAME = "app.scheduler.tasks.run_pokemon_center_collector"


POKEMON_CENTER_SOURCE_COLLECTOR_KEY = "pokemon_center_online"


@celery_app.task(name=POKEMON_CENTER_TASK_NAME)
def run_pokemon_center_collector() -> dict:
    """6時間に1回、ポケモンセンターオンラインの新商品一覧を巡回する(段階2、2026-08-06)。

    段階1(app/collectors/sources/pokemon_center_online.py:discover_new_products())が
    新商品一覧ページから商品コードを抽出し、各商品の個別ページをfetch_product()で
    取得・正規化するところまでを実装済み。本タスクはrun_ichiban_kuji_collector()と
    同じ設計で、discover_new_products()の結果をingest_normalized_item()(タスク13、
    変更無し)でDBへ反映する。ポケセンも一番くじと同じく仕入れ側(SourceCollector)で
    相場データを持たないため、Opportunity生成・通知判定はここでは行わない
    (相場側のrun_suruga_ya_price_rising_scanが、本タスクにより永続化されたProductと
    match_observation_to_product()で突き合わせた時点で自然に行われる。モジュール
    docstring「スコープに関する注記」と同じ結合点)。

    一番くじと異なり、ポケセンは商品コード(13桁)の先頭2桁により条件付きでJANコードを
    identifiersへ供給する(app/collectors/sources/pokemon_center_online.py参照)ため、
    重複防止において一番くじより強く効くことが期待される
    (tests/integration/test_incident_recollection_dedup.pyで確認した一番くじの
    limitationとの対比、CLAUDE.md 13.1節参照)。
    """
    started_at = datetime.now(tz=JST)
    collector = PokemonCenterOnlineCollector()
    errors: list[str] = []
    normalized_items: list = []

    try:
        normalized_items, discover_errors = asyncio.run(collector.discover_new_products())
        errors.extend(discover_errors)
    except (FetchError, ParseError) as exc:
        errors.append(f"discover_new_products failed: {exc}")

    success_count = 0
    ingested_count = 0
    # ichiban_kuji側と同じ理由(モジュールdocstring参照): 取得できたアイテムが1件も
    # 無い場合はDBセッションを開かない。
    if normalized_items:
        session = SessionLocal()
        try:
            source = (
                session.query(Source).filter_by(collector_key=POKEMON_CENTER_SOURCE_COLLECTOR_KEY).one_or_none()
            )
            if source is None:
                source = Source(
                    name="ポケモンセンターオンライン",
                    base_url="https://www.pokemoncenter-online.com",
                    collector_key=POKEMON_CENTER_SOURCE_COLLECTOR_KEY,
                )
                session.add(source)
                session.flush()

            for item in normalized_items:
                field_errors = collector.validate(item)
                if field_errors:
                    errors.append(f"validation failed for '{item.product_name}': {'; '.join(field_errors)}")
                    continue
                success_count += 1
                ingest_normalized_item(session, source, item)
                ingested_count += 1

            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("[pokemon_center_online] DB ingest failed")
            errors.append(f"ingest failed: {type(exc).__name__}: {exc}")
            ingested_count = 0
        finally:
            session.close()

    error_count = len(errors)
    if error_count == 0:
        health = CollectorHealth.OK
    elif success_count > 0:
        health = CollectorHealth.DEGRADED
    else:
        health = CollectorHealth.FAILING

    logger.info(
        "pokemon_center_online collector run finished: health=%s success=%d error=%d ingested=%d errors=%s",
        health.value,
        success_count,
        error_count,
        ingested_count,
        errors,
    )
    status = "success" if health in (CollectorHealth.OK, CollectorHealth.DEGRADED) else "failure"
    record_collector_run(
        POKEMON_CENTER_TASK_NAME,
        started_at,
        status=status,
        summary=f"health={health.value} success={success_count} error={error_count} ingested={ingested_count}",
        error_message="; ".join(errors) if status == "failure" and errors else None,
    )
    return {
        "source_name": collector.source_name,
        "health": health.value,
        "success_count": success_count,
        "error_count": error_count,
        "ingested_count": ingested_count,
    }


SURUGA_YA_SCAN_TASK_NAME = "app.scheduler.tasks.run_suruga_ya_price_rising_scan"


def _match_and_score_observation(
    session: Session, redis_client, observation: MarketObservation, channel_name: str
) -> list[dict]:
    """MarketObservationを既存Productへ照合し(match_observation_to_product、タスク13・
    変更無し)、一致すれば紐づく各release_events(価格確定済み・未削除のもの)について
    Opportunity生成・通知判定まで行う(build_and_score_opportunity/evaluate_notification、
    いずれもタスク13の実装そのまま呼び出す。新規ロジックはこの関数によるオーケストレーション
    のみ)。should_send=Trueの結果のみdictのリストとして返す(Discord送信はしない、
    モジュールdocstring参照)。

    1つのProductが複数release_events(異なる店舗・日程)を持つことは技術分析9章の
    設計上問題無いため(app/pipeline/manual_review.py等でも前提にしている挙動)、
    全件についてOpportunityを生成する。
    """
    match = match_observation_to_product(session, observation)
    if match is None:
        return []
    matched_product, market_match_status = match

    release_events = (
        session.query(ReleaseEvent)
        .filter(
            ReleaseEvent.product_id == matched_product.id,
            ReleaseEvent.deleted_at.is_(None),
            ReleaseEvent.price.isnot(None),
        )
        .all()
    )

    results: list[dict] = []
    for release_event in release_events:
        shop = session.get(Shop, release_event.shop_id)
        source = session.get(Source, release_event.source_id)

        build_result = build_and_score_opportunity(
            session,
            release_event=release_event,
            product=matched_product,
            observation=observation,
            match_status=market_match_status,
            channel_name=channel_name,
        )
        if build_result is None:
            continue

        decision = evaluate_notification(
            redis_client,
            release_event=release_event,
            shop=shop,
            product=matched_product,
            source=source,
            observation=observation,
            standard_displayed_profit=build_result.standard_displayed_profit,
            standard_excluded_cost_items=build_result.standard_excluded_cost_items,
            match_status=market_match_status,
            target_prefectures=set(),
        )
        if decision.should_send:
            manual_review_task_id = _pending_manual_review_task_id(session, build_result.opportunity)
            # 2026-08-06(緊急対応): should_send=Trueの結果をタスクの戻り値に含めるだけでは
            # 誰も自動的に拾って送信しない(常時起動Botが定期的に取得して送信する仕組みが
            # 存在しなかった、CLAUDE.md参照)。pending_notificationsへ永続化し、
            # app/notification/dispatcher.py:dispatch_pending_notifications()が
            # 常時起動Bot側から定期的に拾って送信する設計にした。
            session.add(
                PendingNotification(
                    opportunity_id=build_result.opportunity.id,
                    release_event_id=release_event.id,
                    channel_id=settings.discord_notify_channel_id,
                    embed=decision.embed,
                    manual_review_task_id=manual_review_task_id,
                    dedupe_key=decision.dedupe_result.dedupe_key if decision.dedupe_result else None,
                    created_at=datetime.now(tz=JST),
                )
            )
            session.flush()
            results.append(
                {
                    "release_event_id": str(release_event.id),
                    "product_name": matched_product.name,
                    "opportunity_id": str(build_result.opportunity.id),
                    "manual_review_task_id": manual_review_task_id,
                    "embed": decision.embed,
                }
            )
    return results


@celery_app.task(name=SURUGA_YA_SCAN_TASK_NAME)
def run_suruga_ya_price_rising_scan() -> dict:
    """CLAUDE.md 1.3: 価格上昇中フィルタでの差分取得を1日1〜2回実行する。

    MarketCollectorはSourceCollectorと異なりrun()を持たない(技術分析10章の設計どおり
    search()/parse_observations()のみ)ため、このタスク内でオーケストレーションする。

    2026-08-06(タスク20): 観測した買取価格を_match_and_score_observation()で
    既存Productと照合し、Opportunity生成・通知判定まで行うようにした
    (モジュールdocstring参照)。
    """
    started_at = datetime.now(tz=JST)
    collector = SurugaYaCollector()
    keyword_results: dict[str, int] = {}
    error_messages: list[str] = []
    error_count = 0
    pending_notifications: list[dict] = []
    all_observations: list[MarketObservation] = []

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
            all_observations.extend(observations)
        except (FetchError, ParseError) as exc:
            error_count += 1
            error_messages.append(f"{keyword}: {exc}")
            logger.warning("suruga_ya price-rising scan failed for keyword=%s: %s", keyword, exc)

    # 全キーワードが失敗し観測値が1件も無い場合は、DB/Redisセッションを開かずに終了する
    # (ichiban_kuji側と同じ理由。モジュールdocstring参照)。
    if all_observations:
        session = SessionLocal()
        redis_client = redis_module.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            for observation in all_observations:
                try:
                    pending_notifications.extend(
                        _match_and_score_observation(session, redis_client, observation, channel_name="suruga_ya")
                    )
                except Exception:
                    logger.exception(
                        "suruga_ya price-rising scan: failed to match/score observation title=%s",
                        observation.extra.get("title"),
                    )
            session.commit()
        finally:
            redis_client.close()
            session.close()

    # 一部キーワードだけ失敗してもタスク自体は完走する設計のため、全キーワードが
    # 失敗した場合のみfailure、1件でも取れていればsuccess(部分成功)として記録する。
    status = "failure" if error_count == len(SURUGA_YA_SCAN_KEYWORDS) else "success"
    record_collector_run(
        SURUGA_YA_SCAN_TASK_NAME,
        started_at,
        status=status,
        summary=(
            f"keyword_results={keyword_results} error_count={error_count} "
            f"notifications={len(pending_notifications)}"
        ),
        error_message="; ".join(error_messages) if error_messages else None,
    )
    return {
        "keyword_results": keyword_results,
        "error_count": error_count,
        "pending_notifications": pending_notifications,
    }


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
        # Discordの「照合を確定する」/「別商品として分離」ボタン(app/notification/
        # interaction_view.pyモジュールdocstring「表示条件の設計判断」参照)の表示条件
        # 判定(2026-08-06、タスク20で共通ヘルパーへ抽出、_pending_manual_review_task_id参照)。
        manual_review_task_id = _pending_manual_review_task_id(session, build_result.opportunity)

        stages["opportunity"] = {
            "ok": True,
            "displayed_profit": str(build_result.standard_displayed_profit),
            "opportunity_id": str(build_result.opportunity.id),
            "manual_review_task_id": manual_review_task_id,
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

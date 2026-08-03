"""SourceCollector抽象基底クラス。

技術分析レポート9章の設計をベースに、実装仕様書5章で追加された
NormalizedItemのfulfillment_type/region_override/region_display_text/
region_sourceを反映する。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

__all__ = [
    "CollectorHealth",
    "SupportedEventType",
    "RawFetchResult",
    "ParsedItem",
    "NormalizedItem",
    "CollectorError",
    "FetchError",
    "ParseError",
    "ValidationError",
    "AuthenticationRequiredError",
    "SourceCollector",
    "CollectorRunResult",
]


class CollectorHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"  # 一部フィールド欠落など
    FAILING = "failing"  # 連続失敗
    DISABLED = "disabled"  # 手動/自動で無効化


@dataclass
class RawFetchResult:
    """fetch()の戻り値。生データ+メタ情報。"""

    url: str
    status_code: int
    html: str | None
    fetched_at: datetime
    from_cache: bool = False


@dataclass
class ParsedItem:
    """parse()の戻り値。サイト固有構造→中間表現。"""

    raw_title: str
    price: Decimal | None
    image_urls: list[str]
    event_type: SupportedEventType
    start_at: datetime | None
    deadline_at: datetime | None
    announce_at: datetime | None
    purchase_limit_at: datetime | None
    apply_url: str | None
    product_url: str
    shop_name: str | None
    extra: dict = field(default_factory=dict)  # サイト固有の付加情報


@dataclass
class NormalizedItem:
    """normalize()の戻り値。DB投入直前の共通フォーマット。

    fulfillment_type/region_override/region_display_text/region_sourceは
    実装仕様書5章で追加されたフィールド。判定できない場合の安全側フォールバック
    (fulfillment_type=ONLINE_SHIPPING, region_source=UNKNOWN)をデフォルト値として
    設定しており、normalize()実装側で明示しなければこのフォールバックが適用される。
    """

    product_name: str
    identifiers: dict[str, str]  # {"jan": "...", "model": "..."}
    category_hint: str | None
    brand_hint: str | None
    parsed: ParsedItem
    confidence_hint: str  # A/B/C/D 推定
    fulfillment_type: FulfillmentType = FulfillmentType.ONLINE_SHIPPING
    region_override: str | None = None
    region_display_text: str | None = None
    region_source: RegionSource = RegionSource.UNKNOWN


class CollectorError(Exception):
    """基底例外"""


class FetchError(CollectorError):
    """ネットワーク/HTTPエラー"""


class ParseError(CollectorError):
    """想定外のHTML構造。構造変更検知のトリガー"""


class ValidationError(CollectorError):
    """必須フィールド欠落"""


class AuthenticationRequiredError(CollectorError):
    """ログインが必要だが未対応"""


@dataclass
class CollectorRunResult:
    source_name: str
    started_at: datetime
    finished_at: datetime
    success_count: int
    error_count: int
    errors: list[str]
    health: CollectorHealth


class SourceCollector(ABC):
    source_name: str
    supported_event_types: list[SupportedEventType]
    authentication_required: bool = False
    default_interval_seconds: int = 3600

    #: 1回のrun()で巡回するURL一覧。技術分析レポート9章のコード例には無いが、
    #: fetch()が単一URLしか受け取らない設計のため、run()が何を取得しにいくかを
    #: 決めるにはCollectorごとの巡回対象リストが必要。class属性としてsource_name等
    #: と同じ形式で持たせる。
    target_urls: list[str] = []

    def __init__(self) -> None:
        self.last_success_at: datetime | None = None
        self.error_count: int = 0
        self.health: CollectorHealth = CollectorHealth.OK

    @abstractmethod
    async def fetch(self, target_url: str) -> RawFetchResult:
        """HTTP取得のみ。リトライ/タイムアウトはここで吸収。"""

    @abstractmethod
    def parse(self, raw: RawFetchResult) -> list[ParsedItem]:
        """サイト固有パース。構造変更時はParseErrorを送出。"""

    @abstractmethod
    def normalize(self, items: list[ParsedItem]) -> list[NormalizedItem]:
        """共通フォーマットへ変換。"""

    def validate(self, item: NormalizedItem) -> list[str]:
        """必須フィールドチェック。エラーメッセージのリストを返す(例外にしない)。"""
        errors = []
        if not item.product_name:
            errors.append("product_name is empty")
        if item.parsed.price is None:
            errors.append("price is missing")
        return errors

    async def health_check(self) -> CollectorHealth:
        """疎通確認用の軽量チェック(トップページ取得等)。

        基底クラスでは直近のrun()結果に基づくhealthをそのまま返す。
        実際の疎通確認が必要なCollectorはオーバーライドすること。
        """
        return self.health

    def rate_limit(self) -> float:
        """1リクエストあたりの最小間隔(秒)。サイトごとに上書き。"""
        return 2.0

    async def run(self) -> CollectorRunResult:
        """fetch→parse→normalize→validateを一連実行し、実行ログを生成する。
        個々のCollector実装はfetch/parse/normalizeのみを書けばよい。"""
        started_at = datetime.now()
        errors: list[str] = []
        parsed_items: list[ParsedItem] = []

        for index, url in enumerate(self.target_urls):
            if index > 0:
                await asyncio.sleep(self.rate_limit())
            try:
                raw = await self.fetch(url)
            except (FetchError, AuthenticationRequiredError) as exc:
                errors.append(f"fetch failed for {url}: {exc}")
                continue

            try:
                parsed_items.extend(self.parse(raw))
            except ParseError as exc:
                errors.append(f"parse failed for {url}: {exc}")
                continue

        normalized_items = self.normalize(parsed_items)

        success_count = 0
        for item in normalized_items:
            field_errors = self.validate(item)
            if field_errors:
                errors.append(f"validation failed for '{item.product_name}': {'; '.join(field_errors)}")
                continue
            success_count += 1

        finished_at = datetime.now()
        error_count = len(errors)

        if error_count == 0:
            health = CollectorHealth.OK
            self.error_count = 0
        elif success_count > 0:
            health = CollectorHealth.DEGRADED
            self.error_count += error_count
        else:
            health = CollectorHealth.FAILING
            self.error_count += error_count

        self.health = health
        if success_count > 0:
            self.last_success_at = finished_at

        return CollectorRunResult(
            source_name=self.source_name,
            started_at=started_at,
            finished_at=finished_at,
            success_count=success_count,
            error_count=error_count,
            errors=errors,
            health=health,
        )

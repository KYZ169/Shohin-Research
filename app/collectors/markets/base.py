"""MarketCollector抽象基底クラス。

技術分析レポート10章の設計をベースにする。MarketObservationには、
タスク6(駿河屋Collector実装)で実際に必要になった`extra`フィールドを追加している
(鑑定品価格・[価格上昇中]タグ等)。元の10章スペックのMarketObservationには
`extra`は無いが、SourceCollector側のParsedItemが同様の目的で`extra: dict`を
持っているのと同じ理由(サイト固有の付加情報を型を増やさず保持するため)で追加した。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.collectors.base import NormalizedItem, RawFetchResult

__all__ = ["MarketDataType", "MarketObservation", "MarketCollector"]


class MarketDataType(str, Enum):
    SOLD_PRICE = "sold_price"  # 成約価格(取得できるサイトのみ)
    LISTING_PRICE = "listing_price"  # 現在出品価格
    BUYBACK_PRICE = "buyback_price"  # 買取価格
    LISTING_COUNT = "listing_count"  # 出品数


@dataclass
class MarketObservation:
    product_ref: str  # 検索に使ったキーワード/識別子
    data_type: MarketDataType
    amount: Decimal | None
    count: int | None  # 出品数/成約件数など
    observed_at: datetime
    source_url: str
    confidence: str  # A/B/C/D
    extra: dict = field(default_factory=dict)  # サイト固有の付加情報(タスク6で追加)


class MarketCollector(ABC):
    channel_name: str
    supported_data_types: list[MarketDataType]
    requires_login: bool = False
    stability: str = "unstable"  # stable/unstable/experimental

    @abstractmethod
    async def search(self, query: str, identifiers: dict[str, str]) -> RawFetchResult:
        """商品検索。JAN優先、無ければ商品名+型番。"""

    @abstractmethod
    def parse_observations(self, raw: RawFetchResult) -> list[MarketObservation]:
        """検索結果から相場観測値を抽出。"""

    def filter_irrelevant(
        self, observations: list[MarketObservation], product_hint: NormalizedItem
    ) -> list[MarketObservation]:
        """無関係商品の除外(タイトル類似度・カテゴリ整合性チェック)。

        技術分析レポート11章の商品照合スコアリングを使った本格実装はProduct Matcher側の
        タスク(CLAUDE.mdタスク7)であるため、基底クラスではフィルタせずそのまま返す。
        """
        return observations

    def rate_limit(self) -> float:
        return 3.0

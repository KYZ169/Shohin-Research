"""共有ドメインEnum定義。

技術分析レポート8章(DB設計)・9章(Collector設計)、実装仕様書1章・5章の定義に
合わせる。DBモデル(app/db/models)とCollector層(app/collectors)の両方から
参照される値はここに一元化し、DBカラムの値とNormalizedItemの値が食い違わない
ようにする。
"""

from enum import Enum


class ShopType(str, Enum):
    ONLINE = "online"
    STORE = "store"


class ShopGranularity(str, Enum):
    SINGLE_STORE = "single_store"
    REGIONAL_CHAIN = "regional_chain"
    NATIONAL_CHAIN = "national_chain"


class ProductIdentifierType(str, Enum):
    JAN = "jan"
    ISBN = "isbn"
    SKU = "sku"
    MODEL = "model"


class SupportedEventType(str, Enum):
    NORMAL_SALE = "normal_sale"
    RESERVATION = "reservation"
    LOTTERY = "lottery"
    RESTOCK = "restock"
    STORE_ARRIVAL = "store_arrival"


class FulfillmentType(str, Enum):
    STORE_PICKUP = "store_pickup"
    ONLINE_SHIPPING = "online_shipping"
    BOTH = "both"


class RegionSource(str, Enum):
    EXACT = "exact"
    PREFECTURE = "prefecture"
    AREA_GROUP = "area_group"
    UNKNOWN = "unknown"


class DeadlineSource(str, Enum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class MatchStatus(str, Enum):
    """技術分析レポート11.1の状態遷移。実装仕様書3章のcalc_score()のmatch_status引数と対応。

    自動一致 → 高確率一致 → 要確認 → (手動確定済み / 別商品) の順に人間の確認が必要になる。
    MANUALLY_CONFIRMEDはスコアからは導出されない(app/matcher/product_matcher.pyの
    classify_match_status()は返さない)。要確認キューを人間が確定させた結果として
    呼び出し側(将来のmanual_review_tasks運用)が設定する状態。
    """

    AUTO_MATCH = "auto_match"  # 自動一致
    HIGH_PROBABILITY_MATCH = "high_probability_match"  # 高確率一致
    NEEDS_REVIEW = "needs_review"  # 要確認
    DIFFERENT_PRODUCT = "different_product"  # 別商品
    MANUALLY_CONFIRMED = "manually_confirmed"  # 手動確定済み


class ProfitScenario(str, Enum):
    """技術分析レポート12.4: 悲観(85%)/標準(100%)/楽観(110%)の3シナリオ。"""

    PESSIMISTIC = "pessimistic"
    STANDARD = "standard"
    OPTIMISTIC = "optimistic"


class LotteryEntryResult(str, Enum):
    """技術分析レポート8.1のlottery_entries.result(pending/won/lost)。"""

    PENDING = "pending"
    WON = "won"
    LOST = "lost"


class ManualReviewTaskStatus(str, Enum):
    """manual_review_tasks.status(2026-08-05追加、MatchStatus.MANUALLY_CONFIRMEDの
    運用先。app/pipeline/manual_review.py参照)。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"  # candidate_productをmatched_productへ統合(マージ)した
    REJECTED = "rejected"  # 別商品と判断し、candidate_productは分離したまま維持

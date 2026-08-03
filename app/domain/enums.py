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

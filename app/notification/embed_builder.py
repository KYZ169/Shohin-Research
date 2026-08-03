"""Discord Embed組み立て(実装仕様書4.1・Prompt 7)。

build_embed()はDBセッション/discord.pyいずれにも依存しない純粋関数。呼び出し側
(将来のNotification Service)がProduct/ReleaseEvent/ProfitSnapshot/MarketObservation
等から`OpportunityView`を組み立てて渡す設計とする(region_resolver.py/
product_matcher.py/opportunity_scorer.pyと同じ「呼び出し側が候補データを渡す」方針)。

【スコープに関する注記】
Prompt 7は「実装仕様書4章のEmbed構成とdedupeロジックを実装してください」
「discord.pyを使用」としているが、このサンドボックス環境にはDiscord Bot
トークンが無く、Discord APIへのネットワーク到達性も未確認(他の外部サイト同様
ブロックされている可能性が高い)。そのため実際にBotを接続してメッセージを送信する
部分(discord_bot.py)は検証不可能な最小限の実装に留め、動作確認は
build_embed()/dedupe.pyのロジック(純粋関数+ローカルRedis)に絞って行う。
ボタン/Interaction処理(実装仕様書14.2、CLAUDE.md PoC-5)は元々未実施のため対象外。
"""

from dataclasses import dataclass
from datetime import datetime

from app.core.time import JST
from app.domain.enums import FulfillmentType, MatchStatus, RegionSource
from app.profit.profit_engine import ExcludedCostItem

__all__ = [
    "OpportunityView",
    "color_by_confidence",
    "fulfillment_label",
    "format_jst",
    "build_embed",
]

CONFIDENCE_COLORS: dict[str, int] = {
    "A": 0x2ECC71,  # 緑
    "B": 0x3498DB,  # 青
    "C": 0xF1C40F,  # 黄
    "D": 0x95A5A6,  # グレー
}

FULFILLMENT_LABELS: dict[FulfillmentType, str] = {
    FulfillmentType.STORE_PICKUP: "店舗受取",
    FulfillmentType.ONLINE_SHIPPING: "郵送",
    FulfillmentType.BOTH: "店舗受取/郵送",
}

REGION_UNKNOWN_LABEL = "地域情報不明(全国対象として表示)"
AREA_GROUP_SUFFIX = "（大まかな範囲のみ判明・要確認）"

# 技術分析レポート11.3原則3: 「高確率一致であっても、初回は必ず通知に『要確認』
# フラグを出し、ユーザーが誤りに気づけるようにする」を満たすための表示ラベル。
# AUTO_MATCH/MANUALLY_CONFIRMEDはラベル無し(=フィールド自体を出さない)。
# HIGH_PROBABILITY_MATCHとNEEDS_REVIEWは警戒レベルが異なるため、文言を明確に
# 分ける(ユーザー指摘: 同じ「要確認」で一括りにしない)。
# 地域側の「要確認」(_build_region_line参照)とは別のフィールド名(「商品照合」)で
# 出すことで、地域の要確認と商品照合の要確認を視覚的に混同しないようにしている。
MATCH_STATUS_LABELS: dict[MatchStatus, str] = {
    MatchStatus.HIGH_PROBABILITY_MATCH: "高確率一致・念のためご確認ください",
    MatchStatus.NEEDS_REVIEW: "要確認・別商品の可能性があります",
}


@dataclass
class OpportunityView:
    """build_embed()向けの表示用ビュー。実装仕様書4.1のopp引数が参照する
    フィールドをそのまま型付けしたもの(専用のDBテーブルではない)。"""

    product_name: str
    apply_url: str | None
    image_url: str | None
    confidence: str  # A/B/C/D
    source_name: str
    purchase_price: int
    best_channel_name: str
    fulfillment_type: FulfillmentType
    region_source: RegionSource
    region_display_text: str | None
    displayed_profit: int
    excluded_cost_items: list[ExcludedCostItem]
    match_status: MatchStatus
    deadline_at: datetime | None
    source_url: str
    recent_sold_count: int | None
    listing_count: int | None
    fetched_at: datetime


def color_by_confidence(confidence: str) -> int:
    """実装仕様書14.1: A=緑,B=青,C=黄,D=グレー。"""
    return CONFIDENCE_COLORS[confidence]


def fulfillment_label(fulfillment_type: FulfillmentType) -> str:
    return FULFILLMENT_LABELS[fulfillment_type]


def format_jst(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%Y年%m月%d日 %H:%M")


def _build_region_line(opp: OpportunityView) -> str:
    if opp.region_source == RegionSource.UNKNOWN:
        region_line = REGION_UNKNOWN_LABEL
    else:
        # 実装仕様書4.1のoriginal実装はregion_display_textをそのまま使うが、
        # region_sourceがunknown以外でもdisplay_textがNoneのケースを想定し
        # フォールバックを追加している(元コードの防御漏れに対する補強)。
        region_line = opp.region_display_text or REGION_UNKNOWN_LABEL

    if opp.region_source == RegionSource.AREA_GROUP:
        region_line += AREA_GROUP_SUFFIX

    return region_line


def _build_match_status_field(match_status: MatchStatus) -> dict | None:
    """商品照合(Product Matcher)の確信度を示す独立フィールド。AUTO_MATCH等、
    確認不要なステータスの場合はNoneを返し、フィールド自体を出さない
    (=Embed上に何も表示しない、というのが「表示なし」の意味)。
    """
    label = MATCH_STATUS_LABELS.get(match_status)
    if label is None:
        return None
    return {"name": "商品照合", "value": label, "inline": True}


def _build_profit_note(opp: OpportunityView) -> str:
    if not opp.excluded_cost_items:
        return f"想定利益: ¥{opp.displayed_profit:,}"

    excluded_lines = "\n".join(f"- {item.value}" for item in opp.excluded_cost_items)
    return (
        f"想定利益: ¥{opp.displayed_profit:,}\n"
        f"この額から下記を引いたものが純利益です(目視確認):\n{excluded_lines}"
    )


def build_embed(opp: OpportunityView) -> dict:
    """実装仕様書4.1のEmbed構成。excluded_cost_itemsの有無で表示を分岐し、
    region_source=area_groupの場合は要確認の注記を付ける。deadline_at=Noneの
    場合は「締切は公式サイトでご確認ください」+リンクで代替する(実装仕様書9章)。

    match_status(HIGH_PROBABILITY_MATCH/NEEDS_REVIEW)の場合は「商品照合」という
    独立フィールドを追加する(技術分析11.3原則3。地域の「要確認」とは別フィールド)。
    """
    deadline_field_value = (
        format_jst(opp.deadline_at)
        if opp.deadline_at is not None
        else f"締切は公式サイトでご確認ください([リンク]({opp.source_url}))"
    )

    fields = [
        {"name": "仕入先", "value": opp.source_name, "inline": True},
        {"name": "仕入価格", "value": f"¥{opp.purchase_price:,}", "inline": True},
        {"name": "最良売却先", "value": opp.best_channel_name, "inline": True},
        {"name": "相場信頼度", "value": opp.confidence, "inline": True},
        {"name": "受取方法", "value": fulfillment_label(opp.fulfillment_type), "inline": True},
        {"name": "対象地域", "value": _build_region_line(opp), "inline": True},
    ]

    match_status_field = _build_match_status_field(opp.match_status)
    if match_status_field is not None:
        fields.append(match_status_field)

    fields.extend(
        [
            {"name": "想定利益", "value": _build_profit_note(opp), "inline": False},
            {"name": "締切日時", "value": deadline_field_value, "inline": True},
            {
                "name": "直近成約件数",
                "value": str(opp.recent_sold_count) if opp.recent_sold_count is not None else "取得不可",
                "inline": True,
            },
            {
                "name": "現在出品数",
                "value": str(opp.listing_count) if opp.listing_count is not None else "取得不可",
                "inline": True,
            },
        ]
    )

    return {
        "title": opp.product_name,
        "url": opp.apply_url,
        "thumbnail": {"url": opp.image_url} if opp.image_url else None,
        "color": color_by_confidence(opp.confidence),
        "fields": fields,
        "footer": {"text": f"最終取得: {format_jst(opp.fetched_at)} / 情報元: {opp.source_url}"},
    }

"""一番くじCollector: 1kuji.com(メイン) + bandaispirits.co.jp(補完・裏取り)。

CLAUDE.md 1.1 / 8.5, 実装仕様書9章の実測結果に基づく:
- 1kuji.com: Bot対策なし。トップページの「PICK UP ITEM」に店頭/オンライン販売日時が
  構造化テキストとして併記されており、fulfillment_typeの判定にそのまま使える。
- bandaispirits.co.jp: Bot対策なし。商品詳細ページで価格・発売日の補完/裏取りに使う。
- on-line.1kuji.com: Bot対策により取得不可(確認済み)。本Collectorは絶対にこのドメインへ
  リクエストしない(CLAUDE.mdタスク4着手前チェックリスト)。

【検証状況に関する重要な注意】
このモジュールのparse()は、実際の生HTMLではなくMarkdown変換済みテキストのFixture
(tests/fixtures/html/1kuji_com_top_20260803.md,
 tests/fixtures/html/bandaispirits_detail_20260803.md)をもとに実装した、
テキストパターンベース(正規表現・行単位マッチ)の抽出ロジックである。
実サイトの生HTMLタグ構造(class名等)に対しては未検証のため、本番のConoHa VPS環境
(このサンドボックスと異なりネットワーク制限がない想定)で実際にfetch()した生HTMLに対して
再検証・調整が必要。特に画像URLの抽出は根拠となる実データが乏しいため暫定実装であり、
要検証。
"""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from selectolax.parser import HTMLParser

from app.collectors.base import (
    FetchError,
    NormalizedItem,
    ParseError,
    ParsedItem,
    RawFetchResult,
    SourceCollector,
)
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

JST = timezone(timedelta(hours=9))

ICHIBAN_KUJI_TOP_URL = "https://1kuji.com/"
BANDAISPIRITS_DETAIL_URL_TEMPLATE = (
    "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id={prd_id}&grp_id=9999"
)

PRODUCT_URL_PATTERN = re.compile(r"^https://1kuji\.com/products/[\w-]+$")

# 実装仕様書9章のSTORE_PATTERN/ONLINE_PATTERNのアプローチを踏襲しつつ、
# Fixture実データ(1kuji_com_top_20260803.md 注記1)に合わせて末尾の定型句まで含めて
# マッチさせる。日時部分を除去した残りを商品名として切り出すために必要な拡張。
STORE_DATE_PATTERN = re.compile(
    r"店頭販売(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日(?:\([^)]*\))?より順次発売予定"
)
ONLINE_DATE_PATTERN = re.compile(
    r"オンライン販売(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日"
    r"\((?P<wd>[^)]*)\)(?P<h>\d{2}):(?P<mi>\d{2})より販売開始予定"
)

# bandaispirits_detail_20260803.md 注記2の推測パターン
BANDAISPIRITS_PRICE_PATTERN = re.compile(r"1回(?P<amount>[\d,]+)円\(税(?P<tax>\d+)％込\)")
# 注記4: 期間表記(〜MM月DD日)のケースもあるが、開始日のみを採用する(TODO参照)
BANDAISPIRITS_DATE_PATTERN = re.compile(r"(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日")


def _parse_pickup_item_text(text: str) -> tuple[date | None, datetime | None, str]:
    """1kuji.comのPICK UP ITEMリンクテキストから
    (店頭販売日, オンライン販売日時, 商品名)を抽出する。

    Fixtureでは「店頭販売YYYY年MM月DD日(曜)より順次発売予定オンライン販売YYYY年MM月DD日
    (曜)HH:MMより販売開始予定商品名」という区切り文字の無い連結テキストになっているため、
    日時部分の正規表現マッチを除去した残りを商品名として扱う。
    """
    store_match = STORE_DATE_PATTERN.search(text)
    online_match = ONLINE_DATE_PATTERN.search(text)

    store_date: date | None = None
    if store_match:
        store_date = date(int(store_match["y"]), int(store_match["mo"]), int(store_match["d"]))

    online_dt: datetime | None = None
    if online_match:
        online_dt = datetime(
            int(online_match["y"]),
            int(online_match["mo"]),
            int(online_match["d"]),
            int(online_match["h"]),
            int(online_match["mi"]),
            tzinfo=JST,
        )

    remaining = text
    matches = [m for m in (store_match, online_match) if m is not None]
    for m in sorted(matches, key=lambda m: m.start(), reverse=True):
        remaining = remaining[: m.start()] + remaining[m.end() :]

    return store_date, online_dt, remaining.strip()


def _fulfillment_type_for(store_date: date | None, online_dt: datetime | None) -> FulfillmentType:
    """実装仕様書1.3/5章: 店頭/オンライン販売日時の有無からfulfillment_typeを判定する。
    どちらも取れない場合は安全側フォールバックとしてONLINE_SHIPPING(4章参照)。
    """
    if store_date is not None and online_dt is not None:
        return FulfillmentType.BOTH
    if store_date is not None:
        return FulfillmentType.STORE_PICKUP
    return FulfillmentType.ONLINE_SHIPPING


class IchibanKujiCollector(SourceCollector):
    """一番くじ公式サイトCollector。

    - 1kuji.com: トップページのPICK UP ITEMセクションから新着シリーズを自動収集する
      (run()の自動巡回対象)。
    - bandaispirits.co.jp: 個別商品の価格・発売日の補完/裏取り用。detail.php?prd_id=...の
      URLはprd_idが事前にわからないと組み立てられない(1kuji.com側では商品slugしか
      分からず、prd_idとの対応関係はこのFixtureの範囲では確認できていない)ため、
      run()の自動巡回対象には含めず、fetch_bandaispirits_detail()で個別に呼び出す
      設計とする。
    """

    source_name = "ichiban_kuji"
    supported_event_types = [SupportedEventType.LOTTERY]
    target_urls = [ICHIBAN_KUJI_TOP_URL]
    default_interval_seconds = 6 * 60 * 60  # CLAUDE.md 8.5.4: 6時間に1回

    async def fetch(self, target_url: str) -> RawFetchResult:
        if "on-line.1kuji.com" in target_url:
            # CLAUDE.mdタスク4着手前チェックリスト: 絶対にこのドメインへリクエストしない
            raise FetchError("on-line.1kuji.comへの自動アクセスは方針により禁止されています")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(target_url)
        except httpx.HTTPError as exc:
            raise FetchError(f"{target_url} の取得に失敗しました: {exc}") from exc

        if response.status_code != 200:
            raise FetchError(f"{target_url} が{response.status_code}を返しました")

        return RawFetchResult(
            url=target_url,
            status_code=response.status_code,
            html=response.text,
            fetched_at=datetime.now(tz=JST),
        )

    def parse(self, raw: RawFetchResult) -> list[ParsedItem]:
        if not raw.html:
            raise ParseError(f"{raw.url}: html本文が空です")

        if "1kuji.com" in raw.url and "bandaispirits" not in raw.url:
            return self._parse_top_page(raw)
        if "bandaispirits.co.jp" in raw.url:
            return [self._parse_bandaispirits_detail(raw)]

        raise ParseError(f"{raw.url}: 未対応のURLです(1kuji.com/bandaispirits.co.jpのみ対応)")

    def _parse_top_page(self, raw: RawFetchResult) -> list[ParsedItem]:
        tree = HTMLParser(raw.html)
        items: list[ParsedItem] = []

        for anchor in tree.css("a"):
            href = anchor.attributes.get("href") or ""
            if not PRODUCT_URL_PATTERN.match(href):
                continue

            text = anchor.text(deep=True, separator="")
            store_date, online_dt, product_name = _parse_pickup_item_text(text)

            if store_date is None and online_dt is None:
                # PICK UP ITEM以外(画像のみのラインナップセクション等)は対象外
                continue

            fulfillment_type = _fulfillment_type_for(store_date, online_dt)
            store_release_at = (
                datetime(store_date.year, store_date.month, store_date.day, tzinfo=JST)
                if store_date is not None
                else None
            )

            items.append(
                ParsedItem(
                    raw_title=product_name,
                    # トップページのPICK UP ITEMには価格情報が無い(Fixture注記参照)。
                    # 価格はbandaispirits.co.jpの詳細ページ等で別途補完する運用とする。
                    price=None,
                    image_urls=[],  # 実データ未確認のためTODO(モジュールdocstring参照)
                    event_type=SupportedEventType.LOTTERY,
                    start_at=store_release_at or online_dt,
                    deadline_at=None,  # CLAUDE.md 1.1: on-line.1kuji.comが取得不可なため常にnull
                    announce_at=None,
                    purchase_limit_at=None,
                    apply_url=href,  # 実装仕様書9章: 組み立てられない場合はproduct_urlで代替
                    product_url=href,
                    shop_name=None,
                    extra={
                        "store_release_at": store_release_at,
                        "online_release_at": online_dt,
                        "fulfillment_type": fulfillment_type,
                    },
                )
            )

        if not items:
            raise ParseError(f"{raw.url}: PICK UP ITEMを1件も抽出できませんでした(構造変更の可能性)")

        return items

    def _parse_bandaispirits_detail(self, raw: RawFetchResult) -> ParsedItem:
        tree = HTMLParser(raw.html)
        text = tree.body.text(deep=True, separator="\n") if tree.body else raw.html
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        price_text = self._value_after_label(lines, "価格")
        release_date_text = self._value_after_label(lines, "発売日")
        title = self._title_before_label(lines, "商品詳細")

        parsed_price: Decimal | None = None
        extra: dict = {}
        if price_text is not None:
            price_match = BANDAISPIRITS_PRICE_PATTERN.search(price_text)
            if price_match:
                try:
                    parsed_price = Decimal(price_match["amount"].replace(",", ""))
                except InvalidOperation:
                    parsed_price = None
            if parsed_price is None:
                # Fixture注記1: 無料配布等、通常フォーマットに合致しないケースがある。
                # 価格を無理にnullで握りつぶさず、生テキストをextraに残して後で確認できるようにする。
                extra["price_raw_text"] = price_text

        release_date: date | None = None
        if release_date_text is not None:
            date_match = BANDAISPIRITS_DATE_PATTERN.search(release_date_text)
            if date_match:
                release_date = date(int(date_match["y"]), int(date_match["mo"]), int(date_match["d"]))
            extra["release_date_raw_text"] = release_date_text
            # TODO(要確認事項): Fixture注記4のとおり「YYYY年MM月DD日(曜)〜MM月DD日(曜)」の
            # ような期間表記が実在する。実装仕様書のrelease_events設計(store_release_at等)は
            # 単一日時想定のため、ここでは開始日のみを採用し終了日は破棄している。
            # 期間をどう保持するかはスキーマ設計側の判断が必要なため、そのままTODOとして残す。

        image_urls: list[str] = []
        first_img = tree.css_first("img")
        if first_img is not None:
            src = first_img.attributes.get("src")
            if src:
                # Fixtureでは商品タイトル直前に商品画像が1枚ある構成だったための暫定実装。
                # 実HTMLでの位置関係は未検証(モジュールdocstring参照)。
                image_urls.append(src)

        return ParsedItem(
            raw_title=title or "",
            price=parsed_price,
            image_urls=image_urls,
            event_type=SupportedEventType.LOTTERY,
            start_at=(
                datetime(release_date.year, release_date.month, release_date.day, tzinfo=JST)
                if release_date is not None
                else None
            ),
            deadline_at=None,
            announce_at=None,
            purchase_limit_at=None,
            apply_url=raw.url,
            product_url=raw.url,
            shop_name=None,
            extra=extra,
        )

    @staticmethod
    def _value_after_label(lines: list[str], label: str) -> str | None:
        try:
            idx = lines.index(label)
        except ValueError:
            return None
        return lines[idx + 1] if idx + 1 < len(lines) else None

    @staticmethod
    def _title_before_label(lines: list[str], label: str) -> str | None:
        try:
            idx = lines.index(label)
        except ValueError:
            return None
        return lines[idx - 1] if idx > 0 else None

    def validate(self, item: NormalizedItem) -> list[str]:
        """1kuji.comのトップページ由来アイテムには価格情報が無いことが判明している
        (Fixture参照)ため、基底クラスの価格必須チェックはこのCollectorでは適用しない。
        価格はbandaispirits.co.jp等の詳細情報で後から補完する運用を前提とする。
        """
        errors = []
        if not item.product_name:
            errors.append("product_name is empty")
        return errors

    def normalize(self, items: list[ParsedItem]) -> list[NormalizedItem]:
        normalized: list[NormalizedItem] = []
        for item in items:
            fulfillment_type = item.extra.get("fulfillment_type", FulfillmentType.ONLINE_SHIPPING)

            normalized.append(
                NormalizedItem(
                    product_name=item.raw_title,
                    identifiers={},
                    category_hint="一番くじ",
                    brand_hint="BANDAI SPIRITS",
                    parsed=item,
                    confidence_hint="B",
                    fulfillment_type=fulfillment_type,
                    region_override=None,
                    region_display_text=None,
                    # CLAUDE.md 1.1/実装仕様書: 地域情報を持たないため常にunknown(=全国扱い)
                    region_source=RegionSource.UNKNOWN,
                )
            )
        return normalized

    async def fetch_bandaispirits_detail(self, prd_id: str) -> ParsedItem:
        """bandaispirits.co.jpの商品詳細ページから価格・発売日を裏取りする。

        1kuji.com側の自動巡回(run())とは独立して、prd_idが判明した際に個別に呼び出す
        想定(理由はクラスdocstring参照)。
        """
        url = BANDAISPIRITS_DETAIL_URL_TEMPLATE.format(prd_id=prd_id)
        raw = await self.fetch(url)
        return self._parse_bandaispirits_detail(raw)

"""ポケモンセンターオンラインCollector(pokemoncenter-online.com)。

CLAUDE.md 1.4、Fixture(pokemon_center_online_product_20260803.md)の実測結果に基づく:
- pokemoncenter-online.com: Bot対策なし、UTF-8、取得可能。
- 商品詳細ページ(https://www.pokemoncenter-online.com/{商品コード}.html)の
  「各種期間」セクションに、抽選応募受け付け期間/抽選結果発表日/購入および支払い期間/
  商品のお届け時期の4種類が日時付きで記載されている。一番くじ(on-line.1kuji.com)で
  取得できなかった締切・当選発表・購入期限が、このサイトでは全部取れる
  (CLAUDE.md 1.4で一番くじと並ぶ最優先候補に格上げされた情報源)。

【検証状況(2026-08-03、本番ConoHa VPSでの実データ検証により更新)】
本番VPSで実際に取得した生HTML(tests/fixtures/raw_html/raw_pokemon_center.html)で
動作検証済み。「各種期間」のラベル+値の並び(`_value_after_label()`によるテキスト
パターンマッチ)は実データでもそのまま機能し、抽選応募受け付け期間/抽選結果発表日/
購入および支払い期間/商品のお届け時期の4フィールドとも正しく抽出できることを確認した。
一方、価格の抽出だけは実データで失敗することが判明し修正した:
- 価格は`<p class="price default"><span class="txt">5,800<small>円</small></span>
  <small class="sml">税込</small></p>`のように、金額の数字と「円」「税込」が別々の
  (入れ子になった)要素に分かれている。`tree.body.text(deep=True, separator="\n")`で
  フラット化すると「5,800」「円」「税込」がそれぞれ別行になり、1行ずつ検索していた
  従来のPRICE_PATTERNでは一致しなかった(price=Noneのまま失敗)。行単位ではなく
  本文全体(text)に対して、空白文字(改行含む)を許容する形でマッチするよう修正した。
- 在庫状況(品切れ/予約等)のラベルも同様の理由で、価格行を境界とした行範囲検索を
  やめ、本文全体から最初に一致した行を採用するよう単純化した(実データでも
  「品切れ」(在庫ラベル)の後、「各種期間」より前の「予約 / 販売期間」という
  別の文脈で"予約"の語が再度出現するが、最初の一致を採用することで正しく
  区別できることを確認済み)。

【商品一覧ページ未確認による制約】
CLAUDE.md 1.4/3節のとおり、商品一覧ページ(カテゴリ別・新着別)のURL・構造が未確認のため、
このCollectorはtarget_urls(run()による自動巡回対象)を持たない。商品コードが判明している
個別ページをfetch_product()で都度取得する運用とし、一覧巡回による自動発見は別タスク
(PoC-6相当)とする(要検証)。

【通常販売ページの構造未確認】
Fixtureは抽選販売商品の1件のみ。Fixture注記2のとおり、通常販売・予約販売ページで
「各種期間」セクション自体が存在しない可能性が高いという推測に基づき、このセクションの
有無でevent_type(LOTTERY/NORMAL_SALE)を判定しているが、通常販売ページの実データによる
裏取りはできていない(要検証)。

【時刻表記についての補足】
Fixture注記1のサンプル正規表現は「時:分」のコロン区切りを想定して書かれていたが、
Fixture本文中の実際の日時表記は「16時00分」のような漢字区切りであり、コロンは
一度も出現しない(サンプル正規表現と本文実測値が食い違っていた)。本モジュールの
PERIOD_RANGE_PATTERN/SINGLE_DATETIME_PATTERNは本文の実測表記(漢字区切り)を採用している。
"""

import re
from datetime import datetime
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
from app.core.time import JST
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType

PRODUCT_URL_TEMPLATE = "https://www.pokemoncenter-online.com/{code}.html"
PRODUCT_URL_PATTERN = re.compile(r"https://www\.pokemoncenter-online\.com/(?P<code>\d{13})\.html")

# Fixture注記: 「5,800円 税込」のような表記。実データでは金額と「円」「税込」が
# 別要素に分かれ、フラット化すると間に改行が入るため、\sで改行も許容する
# (本文全体に対してsearchする。行単位のマッチはしない)。
PRICE_PATTERN = re.compile(r"(?P<amount>[\d,]+)\s*円\s*税込")
# Fixture注記: 「お一人様 1点限り」のような表記。
PURCHASE_LIMIT_COUNT_PATTERN = re.compile(r"お一人様\s*(?P<count>\d+)点限り")

# Fixture注記3: 品切れ/予約等のステータスラベル。この1件のFixtureだけでは
# バリエーションを網羅できていないため、要検証。
INVENTORY_STATUS_KEYWORDS = ["品切れ", "予約", "販売中"]

# Fixture注記1: 「各種期間」内の各項目はラベル行(「・」始まり)の直後に日時が続く形式。
APPLY_PERIOD_LABEL = "・抽選応募受け付け期間"
ANNOUNCE_DATE_LABEL = "・抽選結果発表日"
PURCHASE_PERIOD_LABEL = "・購入および、支払い期間"
DELIVERY_TIMING_LABEL = "・商品のお届け時期"

# 区切り文字は全角「～」での確認のみだが、半角「~」「〜」も念のため許容する
# (Fixture注記1: 「実際の区切り文字は本番HTML取得時に要再確認」)。
_TILDE = r"[~〜～]"
# Fixture注記1のサンプル正規表現は「時:分」のコロン区切りを想定していたが、
# 実際のFixture本文は「16時00分」のような漢字区切りであり、コロン表記は出現しない
# (サンプル正規表現とFixture本文の不一致。本文の実測値を優先する)。
PERIOD_RANGE_PATTERN = re.compile(
    r"(?P<sy>\d{4})年(?P<smo>\d{1,2})月(?P<sd>\d{1,2})日.*?(?P<sh>\d{1,2})時(?P<smi>\d{2})分"
    rf"{_TILDE}"
    r"(?P<ey>\d{4})年(?P<emo>\d{1,2})月(?P<ed>\d{1,2})日.*?(?P<eh>\d{1,2})時(?P<emi>\d{2})分"
)
# 抽選結果発表日は単一時刻+「以降」(起点であり終了時刻ではない、Fixture注記1)。
SINGLE_DATETIME_PATTERN = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{1,2})月(?P<d>\d{1,2})日.*?(?P<h>\d{1,2})時(?P<mi>\d{2})分"
)


def _value_after_label(lines: list[str], label: str) -> str | None:
    try:
        idx = lines.index(label)
    except ValueError:
        return None
    return lines[idx + 1] if idx + 1 < len(lines) else None


def _datetime_from_match(match: re.Match, prefix: str = "") -> datetime:
    return datetime(
        int(match[f"{prefix}y"]),
        int(match[f"{prefix}mo"]),
        int(match[f"{prefix}d"]),
        int(match[f"{prefix}h"]),
        int(match[f"{prefix}mi"]),
        tzinfo=JST,
    )


class PokemonCenterOnlineCollector(SourceCollector):
    """ポケモンセンターオンライン商品詳細ページCollector。

    一覧ページが未確認のため(モジュールdocstring参照)、target_urlsによる自動巡回は
    行わず、fetch_product()で商品コードを指定して個別に取得する運用とする。
    """

    source_name = "pokemon_center_online"
    supported_event_types = [SupportedEventType.LOTTERY, SupportedEventType.NORMAL_SALE]
    target_urls: list[str] = []
    # 要検証: 巡回頻度を裏付ける実データ根拠がないため、一番くじCollectorと同程度の暫定値とする。
    default_interval_seconds = 3 * 60 * 60

    async def fetch(self, target_url: str) -> RawFetchResult:
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

        return [self._parse_product_detail(raw)]

    def _parse_product_detail(self, raw: RawFetchResult) -> ParsedItem:
        tree = HTMLParser(raw.html)
        text = tree.body.text(deep=True, separator="\n") if tree.body else raw.html
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        title_node = tree.css_first("h1")
        title = title_node.text(strip=True) if title_node is not None else ""
        if not title:
            raise ParseError(f"{raw.url}: 商品タイトル(h1)を抽出できませんでした(構造変更の可能性)")

        price: Decimal | None = None
        price_match = PRICE_PATTERN.search(text)
        if price_match:
            try:
                price = Decimal(price_match["amount"].replace(",", ""))
            except InvalidOperation:
                price = None

        # 実データ確認済み(raw_pokemon_center.html): 在庫状況ラベルは商品名の直後、
        # 価格より前に出現する。「予約」は「各種期間」より前の別の文脈
        # (「予約 / 販売期間 ...」)でも再度出現するが、最初に一致した行を
        # 採用することで在庫状況ラベルの方を正しく拾える。
        inventory_status: str | None = None
        for line in lines:
            if line in INVENTORY_STATUS_KEYWORDS:
                inventory_status = line
                break

        purchase_limit_count: int | None = None
        limit_match = PURCHASE_LIMIT_COUNT_PATTERN.search(text)
        if limit_match:
            purchase_limit_count = int(limit_match["count"])

        deadline_at: datetime | None = None
        application_start_at: datetime | None = None
        apply_period_text = _value_after_label(lines, APPLY_PERIOD_LABEL)
        if apply_period_text is not None:
            range_match = PERIOD_RANGE_PATTERN.search(apply_period_text)
            if range_match:
                application_start_at = _datetime_from_match(range_match, "s")
                deadline_at = _datetime_from_match(range_match, "e")

        announce_at: datetime | None = None
        announce_text = _value_after_label(lines, ANNOUNCE_DATE_LABEL)
        if announce_text is not None:
            announce_match = SINGLE_DATETIME_PATTERN.search(announce_text)
            if announce_match:
                # Fixture注記1: 「以降」表記のため単一時刻は起点。終了時刻ではない。
                announce_at = _datetime_from_match(announce_match)

        purchase_limit_at: datetime | None = None
        purchase_start_at: datetime | None = None
        purchase_period_text = _value_after_label(lines, PURCHASE_PERIOD_LABEL)
        if purchase_period_text is not None:
            range_match = PERIOD_RANGE_PATTERN.search(purchase_period_text)
            if range_match:
                purchase_start_at = _datetime_from_match(range_match, "s")
                purchase_limit_at = _datetime_from_match(range_match, "e")

        delivery_timing_text = _value_after_label(lines, DELIVERY_TIMING_LABEL)

        # Fixture注記2: 「各種期間」の抽選応募受け付け期間ブロックの有無で
        # event_type=lotteryかどうかを一次判定する(通常販売ページの実データ未確認のため要検証)。
        event_type = SupportedEventType.LOTTERY if apply_period_text is not None else SupportedEventType.NORMAL_SALE

        code_match = PRODUCT_URL_PATTERN.match(raw.url)
        product_code = code_match["code"] if code_match else None

        image_urls: list[str] = []
        first_img = tree.css_first("img")
        if first_img is not None:
            src = first_img.attributes.get("src")
            if src:
                image_urls.append(src)  # 実データ未確認のため暫定実装、要検証

        extra: dict = {
            "product_code": product_code,
            "inventory_status": inventory_status,
            "purchase_limit_count": purchase_limit_count,
            "application_start_at": application_start_at,
            "purchase_start_at": purchase_start_at,
            # Fixture注記5: ページ上部の「お届け予定日」欄と表記ゆれがあるため、
            # 「各種期間」内の「商品のお届け時期」の生テキストをそのまま保持する。
            "delivery_timing_text": delivery_timing_text,
        }

        return ParsedItem(
            raw_title=title,
            price=price,
            image_urls=image_urls,
            event_type=event_type,
            start_at=application_start_at,
            deadline_at=deadline_at,
            announce_at=announce_at,
            purchase_limit_at=purchase_limit_at,
            apply_url=raw.url,
            product_url=raw.url,
            shop_name=None,
            extra=extra,
        )

    def normalize(self, items: list[ParsedItem]) -> list[NormalizedItem]:
        normalized: list[NormalizedItem] = []
        for item in items:
            # 締切・当選発表・購入期限まで確認できるlotteryは信頼度A、
            # それ以外(各種期間セクションが無い=通常販売と推定)はichiban_kujiと同程度のB。
            confidence_hint = "A" if item.event_type == SupportedEventType.LOTTERY else "B"

            normalized.append(
                NormalizedItem(
                    product_name=item.raw_title,
                    identifiers={},  # Fixture注記4: 商品コードがJANコードかは未確認のため設定しない
                    category_hint="ポケモンカードゲーム",
                    brand_hint="ポケモン",
                    parsed=item,
                    confidence_hint=confidence_hint,
                    fulfillment_type=FulfillmentType.ONLINE_SHIPPING,
                    region_override=None,
                    region_display_text=None,
                    # 通販サイトであり店舗情報を持たないため常にunknown(=全国扱い)
                    region_source=RegionSource.UNKNOWN,
                )
            )
        return normalized

    async def fetch_product(self, product_code: str) -> NormalizedItem:
        """商品コードを指定して個別ページを取得・パース・正規化する。

        一覧ページが未確認のため(モジュールdocstring参照)、run()による自動巡回とは
        独立して、商品コードが判明した際に個別に呼び出す想定。
        """
        url = PRODUCT_URL_TEMPLATE.format(code=product_code)
        raw = await self.fetch(url)
        parsed_items = self.parse(raw)
        return self.normalize(parsed_items)[0]

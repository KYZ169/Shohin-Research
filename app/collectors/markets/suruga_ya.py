"""駿河屋買取Market Collector。

技術分析レポート3.2/10章、CLAUDE.md 1.3の実測結果に基づく:
- suruga-ya.jp: Bot対策なし、UTF-8。買取価格ページの取得安定性は「安定」評価。
- 検索エンドポイント: https://www.suruga-ya.jp/kaitori/search_buy
  (category, search_word, restrict[]クエリパラメータで絞り込み可能)

【検証状況に関する重要な注意】
このモジュールのparse_observations()は、実際の生HTMLではなくMarkdown変換済み
テキストのFixture(tests/fixtures/html/suruga_ya_kaitori_search_20260803.md)を
もとに実装した、テキストパターンベース(正規表現)の抽出ロジックである。
Fixtureが「markdownのパイプテーブル」として表現されていることから、実サイトも
<table><tr>構造である可能性が高いと推測して`tr`要素を行の単位として扱っているが、
実際のクラス名・DOM構造そのものへの検証は未実施。本番のConoHa VPS環境で実際に
fetch()した生HTMLに対して再検証・調整が必要
(app/collectors/sources/ichiban_kuji.py と同様の注意点)。
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from selectolax.parser import HTMLParser

from app.collectors.base import FetchError, ParseError, RawFetchResult
from app.collectors.markets.base import MarketCollector, MarketDataType, MarketObservation
from app.core.time import JST

SEARCH_URL = "https://www.suruga-ya.jp/kaitori/search_buy"

# CLAUDE.md 1.3で確認済みのカテゴリID
CATEGORY_HOBBY_ALL = "501"
CATEGORY_TRADING_CARDS = "50108"
CATEGORY_PLASTIC_MODELS = "50104"
CATEGORY_FIGURES = "50102"
CATEGORY_TRADING_FIGURES = "50103"

DETAIL_URL_PATTERN = re.compile(r"https://www\.suruga-ya\.jp/kaitori/kaitori_detail/(?P<code>[A-Za-z0-9]+)")
# Fixture注記5
MANAGEMENT_NUMBER_PATTERN = re.compile(r"GU\d+")
RELEASE_DATE_PATTERN = re.compile(r"(?P<y>\d{4})/(?P<mo>\d{2})/(?P<d>\d{2})")
# Fixture注記2: 通常価格(無鑑定品)
PRICE_PATTERN = re.compile(r"(?P<amount>[\d,]+)円")
# Fixture注記3: 鑑定品価格の併記(例: 【PSA/GEM MT 10】： 9,000円)
GRADED_PRICE_PATTERN = re.compile(r"【(?P<grade>[^】]+)】[：:]\s*(?P<amount>[\d,]+)円")
QUOTE_REQUIRED_TEXT = "メールにてお見積"
PRICE_RISING_TAG = "価格上昇中"


def _decimal_from_amount_text(amount_text: str) -> Decimal | None:
    try:
        return Decimal(amount_text.replace(",", ""))
    except InvalidOperation:
        return None


class SurugaYaCollector(MarketCollector):
    """駿河屋買取(kaitori/search_buy)からbuyback_priceを取得するMarketCollector。"""

    channel_name = "suruga_ya"
    supported_data_types = [MarketDataType.BUYBACK_PRICE]
    requires_login = False
    stability = "stable"  # 技術分析レポート3.2: 取得安定性「安定」評価

    def __init__(self, category: str = CATEGORY_HOBBY_ALL) -> None:
        self.category = category

    async def search(
        self, query: str, identifiers: dict[str, str], restrict: list[str] | None = None
    ) -> RawFetchResult:
        """商品検索。JAN優先、無ければ型番、それも無ければ商品名(query)で検索する。"""
        search_word = identifiers.get("jan") or identifiers.get("model") or query

        params = [("category", self.category), ("search_word", search_word)]
        for r in restrict or []:
            params.append(("restrict[]", r))
        url = f"{SEARCH_URL}?{urlencode(params)}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"{url} の取得に失敗しました: {exc}") from exc

        if response.status_code != 200:
            raise FetchError(f"{url} が{response.status_code}を返しました")

        return RawFetchResult(
            url=url,
            status_code=response.status_code,
            html=response.text,
            fetched_at=datetime.now(tz=JST),
        )

    def parse_observations(self, raw: RawFetchResult) -> list[MarketObservation]:
        if not raw.html:
            raise ParseError(f"{raw.url}: html本文が空です")

        product_ref = self._product_ref_from_url(raw.url)
        tree = HTMLParser(raw.html)
        observations: list[MarketObservation] = []

        for row in tree.css("tr"):
            detail_anchor = self._find_detail_anchor(row)
            if detail_anchor is None:
                continue  # ヘッダ行など、商品詳細リンクを持たない行はスキップ

            detail_url = detail_anchor.attributes.get("href", "")
            row_text = row.text(deep=True, separator="\n")

            observations.append(self._build_observation(product_ref, detail_url, row_text))

        if not observations:
            raise ParseError(f"{raw.url}: 買取価格を1件も抽出できませんでした(構造変更の可能性)")

        return observations

    def _build_observation(self, product_ref: str, detail_url: str, row_text: str) -> MarketObservation:
        code_match = DETAIL_URL_PATTERN.match(detail_url)
        management_number = code_match["code"] if code_match else None
        if management_number is None:
            number_match = MANAGEMENT_NUMBER_PATTERN.search(row_text)
            management_number = number_match.group() if number_match else None

        release_date: date | None = None
        date_match = RELEASE_DATE_PATTERN.search(row_text)
        if date_match:
            release_date = date(int(date_match["y"]), int(date_match["mo"]), int(date_match["d"]))

        extra: dict = {
            "management_number": management_number,
            "detail_url": detail_url,
            "release_date": release_date,
            # Fixture注記1: 一覧ページだけではJANコードの実値が確認できていないためTODO。
            # 詳細ページ(kaitori_detail/{管理番号})への追加アクセスが必要かは本番環境で要確認。
            "jan": None,
        }
        if PRICE_RISING_TAG in row_text:
            extra["trend"] = "price_rising"

        graded_match = GRADED_PRICE_PATTERN.search(row_text)
        if graded_match:
            extra["graded_price"] = {
                "grade": graded_match["grade"],
                "amount": _decimal_from_amount_text(graded_match["amount"]),
            }

        if QUOTE_REQUIRED_TEXT in row_text:
            # Fixture注記2: 絶対に0円やダミー値を入れず、amount=Noneのまま
            # 「買取価格取得不可」として保持する。confidenceはDへ格下げ。
            extra["quote_required"] = True
            return MarketObservation(
                product_ref=product_ref,
                data_type=MarketDataType.BUYBACK_PRICE,
                amount=None,
                count=None,
                observed_at=datetime.now(tz=JST),
                source_url=detail_url,
                confidence="D",
                extra=extra,
            )

        # Fixture注記3: 鑑定品価格が併記されている場合、先頭に出現する金額が無鑑定品の価格。
        # GRADED_PRICE_PATTERNにマッチする部分より前で最初に見つかる金額を採用する。
        graded_start = graded_match.start() if graded_match else len(row_text)
        price_match = PRICE_PATTERN.search(row_text[:graded_start])
        amount = _decimal_from_amount_text(price_match["amount"]) if price_match else None

        return MarketObservation(
            product_ref=product_ref,
            data_type=MarketDataType.BUYBACK_PRICE,
            amount=amount,
            count=None,
            observed_at=datetime.now(tz=JST),
            source_url=detail_url,
            # 技術分析レポート13.1: 買取価格が取得できている場合は信頼度B。
            # 価格が結局取れなかった場合(想定外のフォーマット)はDに格下げする。
            confidence="B" if amount is not None else "D",
            extra=extra,
        )

    @staticmethod
    def _find_detail_anchor(row):
        """行内のkaitori_detailリンクのうち、「詳細」というナビゲーションテキストではない
        (=タイトルリンクである)ものを優先して返す。Fixture注記1: タイトル列のリンクテキストは
        `型番[レアリティ]：カード名`であり、行末の「詳細」リンクと区別する必要がある。
        """
        candidates = [a for a in row.css("a") if DETAIL_URL_PATTERN.match(a.attributes.get("href", ""))]
        if not candidates:
            return None
        for anchor in candidates:
            if anchor.text(strip=True) != "詳細":
                return anchor
        return candidates[0]

    @staticmethod
    def _product_ref_from_url(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        return query.get("search_word", [""])[0]

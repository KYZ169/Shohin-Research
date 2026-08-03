"""駿河屋買取Market Collector。

技術分析レポート3.2/10章、CLAUDE.md 1.3の実測結果に基づく:
- suruga-ya.jp: Bot対策なし、UTF-8。買取価格ページの取得安定性は「安定」評価。
- 検索エンドポイント: https://www.suruga-ya.jp/kaitori/search_buy
  (category, search_word, restrict[]クエリパラメータで絞り込み可能)

【検証状況(2026-08-03、本番ConoHa VPSでの実データ検証により更新)】
本番VPSで実際に取得した生HTML(tests/fixtures/raw_html/raw_suruga_ya_search.html)で
動作検証済み。`<table><tr>`構造である推測自体は正しかったが、以下2点が実データと
不一致だったため修正した(前回報告済みの`_find_detail_anchor()`関連バグの原因もこれ):
- 商品詳細ページへのリンク(`href`)は`https://www.suruga-ya.jp/kaitori/kaitori_detail/
  {管理番号}`という絶対URLではなく、`/kaitori/kaitori_detail/{管理番号}`という
  相対パスだった。`DETAIL_URL_PATTERN`がこの相対パスにマッチせず、行内の候補アンカーが
  1件も見つからないまま全行がスキップされ「買取価格を1件も抽出できませんでした」で
  失敗していた。
- `<a>`要素の一部(全選択チェックボックス用のリンク等)は`href=""`のような空文字列の
  属性を持つが、selectolaxはこれを`None`として返すことがあり、
  `a.attributes.get("href", "")`では拾いきれず`DETAIL_URL_PATTERN.match(None)`で
  `TypeError`になっていた。`or ""`で明示的にNoneを吸収するよう修正した。
価格・JANコード・鑑定品価格の併記・[価格上昇中]タグの抽出パターンは実データでも
そのまま機能することを確認済み(タグはfont/strongタグで装飾されているが
`row.text()`でフラット化した時点では従来通り文字列として現れるため無変更)。
ただし鑑定品価格(【PSA/GEM MT 10】等)については、今回取得した実HTMLに該当商品が
含まれていなかったため実データでの動作確認はできていない(要検証)。
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from app.collectors.base import FetchError, ParseError, RawFetchResult
from app.collectors.markets.base import MarketCollector, MarketDataType, MarketObservation
from app.core.time import JST

SEARCH_URL = "https://www.suruga-ya.jp/kaitori/search_buy"
BASE_URL = "https://www.suruga-ya.jp"

# CLAUDE.md 1.3で確認済みのカテゴリID
CATEGORY_HOBBY_ALL = "501"
CATEGORY_TRADING_CARDS = "50108"
CATEGORY_PLASTIC_MODELS = "50104"
CATEGORY_FIGURES = "50102"
CATEGORY_TRADING_FIGURES = "50103"

# 実データ確認済み(raw_suruga_ya_search.html): hrefは絶対URLではなく
# "/kaitori/kaitori_detail/{code}"という相対パス。念のため絶対URL表記も許容する。
DETAIL_URL_PATTERN = re.compile(r"(?:https://www\.suruga-ya\.jp)?/kaitori/kaitori_detail/(?P<code>[A-Za-z0-9]+)")
# Fixture(suruga_ya_kaitori_search_20260803.md)注記5。
# suruga_ya_jan_confirmation_20260803.md注記3で、管理番号には"GU"+数字だけでなく
# 数字のみの形式(雑貨・小物カテゴリ、例:"608000898")もあることが確認されている。
# ただしテキストからの素の数字マッチはJANコード(13桁)と衝突しうるため、
# 管理番号は基本的にDETAIL_URL_PATTERN(href由来、GU/数字どちらの形式も
# [A-Za-z0-9]+でカバー済み)で取得し、このパターンはGU形式のテキストフォールバックに限定する。
MANAGEMENT_NUMBER_PATTERN = re.compile(r"GU\d+")
# suruga_ya_jan_confirmation_20260803.md注記1: 「発売日/型番/JANコード/管理番号」列に
# 13桁の数字(JAN)と管理番号がスペース区切りで併記されるケースがある。
# 例: "4983164650440     608000898"
JAN_AND_ID_PATTERN = re.compile(r"(?P<jan>\d{13})\s+(?P<id>\S+)")
# 同注記4: 画像URLのshinabanパラメータも管理番号と一致する(例: shinaban=608000898001)ため、
# フォールバックとして使える、との示唆があった。ただし現在の実装では_find_detail_anchor()と
# _build_observation()のcode_match抽出が同じDETAIL_URL_PATTERNを使っており、アンカーが
# 見つかった時点でcode_matchも必ず成功する(=URL由来の抽出が失敗してshinabanへフォール
# バックする経路が到達不能)。実装してもテストできない死んだコードになるため、
# 現時点ではフォールバックとして実装しない(要検討: 将来DOM構造の違いでアンカー検出と
# コード抽出が分離しうる場合に再検討する)。そのため画像src取得用のヘルパーや
# shinaban抽出用の正規表現定数もあえて追加していない。
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

            href = detail_anchor.attributes.get("href") or ""
            # 実データ確認済み(raw_suruga_ya_search.html): hrefは相対パスのため、
            # 絶対URLに変換してsource_url等に保持する。
            detail_url = urljoin(BASE_URL, href)
            title = detail_anchor.text(strip=True)
            row_text = row.text(deep=True, separator="\n")

            observations.append(self._build_observation(product_ref, detail_url, title, row_text))

        if not observations:
            raise ParseError(f"{raw.url}: 買取価格を1件も抽出できませんでした(構造変更の可能性)")

        return observations

    def _build_observation(
        self,
        product_ref: str,
        detail_url: str,
        title: str,
        row_text: str,
    ) -> MarketObservation:
        code_match = DETAIL_URL_PATTERN.match(detail_url)
        management_number = code_match["code"] if code_match else None
        if management_number is None:
            number_match = MANAGEMENT_NUMBER_PATTERN.search(row_text)
            management_number = number_match.group() if number_match else None

        release_date: date | None = None
        date_match = RELEASE_DATE_PATTERN.search(row_text)
        if date_match:
            release_date = date(int(date_match["y"]), int(date_match["mo"]), int(date_match["d"]))

        # suruga_ya_jan_confirmation_20260803.md注記1: 一覧ページの時点でJANコードが
        # 取得できるケースがある(13桁の数字)。注記2のとおり全商品にあるわけではないため
        # nullable前提は変えず、取れた場合のみ設定する。
        jan_match = JAN_AND_ID_PATTERN.search(row_text)
        jan = jan_match["jan"] if jan_match else None

        extra: dict = {
            "title": title,  # Product Matcher(app/matcher/product_matcher.py)での商品名照合に使う
            "management_number": management_number,
            "detail_url": detail_url,
            "release_date": release_date,
            "jan": jan,
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
        candidates = [a for a in row.css("a") if DETAIL_URL_PATTERN.match(a.attributes.get("href") or "")]
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

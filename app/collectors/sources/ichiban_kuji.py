"""一番くじCollector: 1kuji.com(メイン) + bandaispirits.co.jp(補完・裏取り)
+ on-line.1kuji.com(商品一覧ページのみ、新商品自動発見用)。

CLAUDE.md 1.1 / 8.5, 実装仕様書9章の実測結果に基づく:
- 1kuji.com: Bot対策なし。トップページの「PICK UP ITEM」に店頭/オンライン販売日時が
  構造化テキストとして併記されており、fulfillment_typeの判定にそのまま使える。
- bandaispirits.co.jp: Bot対策なし。商品詳細ページで価格・発売日の補完/裏取りに使う。
- on-line.1kuji.com: 商品詳細・応募ページ(ProductDetail.aspx等)はBot対策により
  取得不可(確認済み)。**ただし商品一覧ページ(ProductList.aspx)のみは例外的に
  取得可能であることを2026-08-06に確認した(詳細は下記検証状況・CLAUDE.md 1.1参照)。**
  本Collectorはこの1URL(`ONLINE_1KUJI_PRODUCT_LIST_URL`)以外、on-line.1kuji.comへは
  絶対にリクエストしない(ホワイトリスト形式、`fetch()`参照)。

【検証状況(2026-08-06、on-line.1kuji.com商品一覧ページの取得可否確認)】
CLAUDE.md 1.1では「on-line.1kuji.comはBot対策により取得不可」と確認済みだったが、
これは商品詳細・応募ページ(ProductDetail.aspx等)を根拠にした判定だった。同じドメイン内の
商品一覧ページ(https://on-line.1kuji.com/Form/Product/ProductList.aspx)を実際に取得した
生HTML(tests/fixtures/raw_html/raw_online_1kuji_productlist.html)を確認したところ、
Bot対策に阻まれず正常に取得でき、確認時点で販売中の全22商品のpid・商品名・価格・
販売期間開始日時が`div.m-product-item`単位で構造化されて含まれていることを確認した
(Cloudflareのbot-management JSスニペットはページに埋め込まれているが、これは
多くのサイトに標準挿入される監視用スクリプトであり、コンテンツ自体のブロックとは
別物。実際に全22件のデータが正常にレスポンス本文に含まれていた)。
p-bandai.jp(トップページは取得可・個別商品ページのみAkamai保護)と同様、
同一ドメイン内でもパス単位で保護の有無が異なる非対称な構成であり、0節方針4
「Bot対策サイトは正面突破しない」はブロックされていないページの利用を妨げない
(取得できないページを無理に突破しないという意味であり、取得できるページまで
避ける趣旨ではない、ユーザー確認済み)。
このページで取得できないもの(締切・当選発表等)は従来通り取得しない。
一番くじONLINEは抽選応募制ではなく購入型のオンラインくじという性質のため、
そもそも「締切」という概念が存在しない可能性があるが、この点は実データからは
確認できていない(要継続検証、CLAUDE.md 3節参照)。deadline_at=Noneのままにすることで、
release_events.deadline_sourceは既存のデフォルト値(DeadlineSource.UNKNOWN)が
自動的に適用される(明示的な追加実装は不要)。

【検証状況(2026-08-03、本番ConoHa VPSでの実データ検証により更新)】
トップページ(_parse_top_page/PICK UP ITEM抽出)は、本番VPSで実際に取得した生HTML
(tests/fixtures/raw_html/raw_1kuji_top.html)で動作検証済み。当初のMarkdown変換済み
テキストFixture(tests/fixtures/html/1kuji_com_top_20260803.md)を前提にした
テキスト連結パターンマッチでは実際のDOM構造と一致せず
「PICK UP ITEMを1件も抽出できませんでした」で失敗することが確認されたため、
実HTMLの構造(`section.pickupCol` > `div.swiper-slide` > `a` +
`div.txtCol` > `p.status`/`p.date`/`p.itemName`)に基づくCSSセレクタベースの
実装に書き直した(実装仕様書9章のCollector基底クラス設計に沿う形)。主な相違点:
- 商品詳細ページへのリンクは絶対URLではなく相対パス(`/products/{slug}`)。
  `urljoin()`で絶対URLに変換する。
- 「店頭販売」「オンライン販売」というラベルは日付テキストと同じ要素内に連結されておらず、
  `<p class="status shop">`/`<p class="status online">`という別要素になっている。
  日付側の`<p class="date">`には「2026年08月08日(土)より発売予定」のように
  ラベル文言を含まない形で記載されている(「より発売予定」と「より順次発売予定」の
  両方の表記が実在することを確認済み)。
- 商品名は`<p class="itemName">`に独立して格納されており、日付テキストを正規表現で
  除去して切り出す必要がなくなった。

【検証状況(2026-08-04、本番ConoHa VPSでの実データ検証により更新)】
bandaispirits.co.jpの商品詳細ページ(_parse_bandaispirits_detail)も、本番VPSで
実際に取得した生HTML(tests/fixtures/raw_html/raw_bandaispirits_detail.html、
鬼滅の刃、通常の一番くじ商品)で動作検証済み。価格・発売日・商品名の抽出
(ラベル行+値行のテキストパターンマッチ)は、当初Markdown変換済みテキストの
Fixture(tests/fixtures/html/bandaispirits_detail_20260803.md)をもとに書いた
推測実装のまま、追加の実装変更無しで実データに対しても正しく機能することを確認した
(「価格」「発売日」「商品詳細」のラベル行はいずれも本文中に1回しか出現せず、
`lines.index()`による最初の一致が誤って別の箇所を拾う心配もない)。
一方、商品画像(image_urls)だけは実データで誤りが判明し修正した:
`tree.css_first("img")`がページ最初の`<img>`要素(ヘッダーの
`/assets/img/logo_01.svg`、BANDAI SPIRITSロゴ)を拾ってしまい、商品画像では
なかった。実際の商品画像は`div.l_productsSlider`内にまとまっており(alt属性が
商品名と一致、先頭が商品全体の代表画像・以降は個別景品の画像)、このコンテナ配下の
`img`から取得するよう修正した。
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser, Node

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

ICHIBAN_KUJI_TOP_URL = "https://1kuji.com/"
BANDAISPIRITS_DETAIL_URL_TEMPLATE = (
    "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id={prd_id}&grp_id=9999"
)
ONLINE_1KUJI_BASE_URL = "https://on-line.1kuji.com"
# モジュールdocstring「検証状況(2026-08-06)」参照。on-line.1kuji.comの中でこの1URLのみ
# fetch()が許可する(ホワイトリスト、完全一致)。ProductDetail.aspx等は引き続き禁止。
ONLINE_1KUJI_PRODUCT_LIST_URL = "https://on-line.1kuji.com/Form/Product/ProductList.aspx"

# 実データ確認済み(raw_1kuji_top.html): 商品詳細へのリンクは相対パス「/products/{slug}」。
PRODUCT_PATH_PATTERN = re.compile(r"^/products/[\w-]+$")

# 実データ確認済み(raw_online_1kuji_productlist.html): 商品詳細/応募ページへのリンクに
# "pid=xxxxxxx"または"pid=sap_0000008524"のようなクエリパラメータで商品IDが入っている。
PID_PATTERN = re.compile(r"[?&]pid=(?P<pid>[^&]+)")
# 実データ確認済み: "&#165;700(税込)"のように金額と「(税込)」が並ぶ(HTML実体参照はselectolaxの
# text()でデコードされ、amountの前に通貨記号やその他の空白が入りうるため金額部分のみを拾う)。
ONLINE_1KUJI_PRICE_PATTERN = re.compile(r"(?P<amount>[\d,]+)\s*\(税込\)")
# 実データ確認済み: "2026年08月05日（水）13:00"のように全角括弧の曜日を挟んで時刻が続く。
ONLINE_1KUJI_SELL_FROM_PATTERN = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日.*?(?P<h>\d{2}):(?P<mi>\d{2})"
)

# 実データ確認済み(raw_1kuji_top.html): 日付テキスト自体には「店頭販売」等のラベルを
# 含まない(ラベルは別要素のp.status)。「より発売予定」「より順次発売予定」の両方の
# 表記を確認済みのため「順次」は任意とする。
STORE_DATE_PATTERN = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日(?:\([^)]*\))?より(?:順次)?発売予定"
)
ONLINE_DATE_PATTERN = re.compile(
    r"(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日"
    r"\((?P<wd>[^)]*)\)(?P<h>\d{2}):(?P<mi>\d{2})より販売開始予定"
)

# bandaispirits_detail_20260803.md 注記2の推測パターン(未検証、モジュールdocstring参照)
BANDAISPIRITS_PRICE_PATTERN = re.compile(r"1回(?P<amount>[\d,]+)円\(税(?P<tax>\d+)％込\)")
# 注記4: 期間表記(〜MM月DD日)のケースもあるが、開始日のみを採用する(TODO参照)
BANDAISPIRITS_DATE_PATTERN = re.compile(r"(?P<y>\d{4})年(?P<mo>\d{2})月(?P<d>\d{2})日")


def _parse_pickup_slide(slide: Node) -> tuple[date | None, datetime | None, str | None]:
    """PICK UP ITEMの1商品(`div.swiper-slide`)から
    (店頭販売日, オンライン販売日時, 商品名)を抽出する。

    実データ確認済み(raw_1kuji_top.html): `div.txtCol`配下に
    `<p class="status shop">店頭販売</p><p class="date">...</p>`
    `<p class="status online">オンライン販売</p><p class="date">...</p>`
    `<p class="itemName">商品名</p>`
    の順でラベルと値のp要素が並ぶ(店頭/オンラインどちらか一方のみの商品も実在する:
    店頭のみ=onep104、オンラインのみ=petitcure、両方=それ以外の大半)。
    直前に出現したp.statusのクラス(shop/online)を見て、続くp.dateをどちらの
    日付として扱うか判定する。
    """
    txt_col = slide.css_first(".txtCol")
    if txt_col is None:
        return None, None, None

    store_date: date | None = None
    online_dt: datetime | None = None
    title: str | None = None
    current_status: str | None = None

    for p in txt_col.css("p"):
        classes = (p.attributes.get("class") or "").split()
        text = p.text(strip=True)

        if "itemName" in classes:
            title = text
        elif "status" in classes:
            if "shop" in classes:
                current_status = "shop"
            elif "online" in classes:
                current_status = "online"
            else:
                current_status = None
        elif "date" in classes:
            if current_status == "shop":
                m = STORE_DATE_PATTERN.search(text)
                if m:
                    store_date = date(int(m["y"]), int(m["mo"]), int(m["d"]))
            elif current_status == "online":
                m = ONLINE_DATE_PATTERN.search(text)
                if m:
                    online_dt = datetime(
                        int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]), tzinfo=JST
                    )

    return store_date, online_dt, title


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
    - on-line.1kuji.com: ProductList.aspx(商品一覧ページ)のみ、新商品の自動発見用に
      run()の自動巡回対象に含める(モジュールdocstring「検証状況(2026-08-06)」参照)。
      ProductDetail.aspx等の個別詳細・応募ページには絶対にアクセスしない
      (fetch()でホワイトリスト形式に制限、CLAUDE.md 1.1参照)。
    - bandaispirits.co.jp: 個別商品の価格・発売日の補完/裏取り用。detail.php?prd_id=...の
      URLはprd_idが事前にわからないと組み立てられない(1kuji.com側では商品slugしか
      分からず、prd_idとの対応関係はこのFixtureの範囲では確認できていない)ため、
      run()の自動巡回対象には含めず、fetch_bandaispirits_detail()で個別に呼び出す
      設計とする。
    """

    source_name = "ichiban_kuji"
    supported_event_types = [SupportedEventType.LOTTERY]
    target_urls = [ICHIBAN_KUJI_TOP_URL, ONLINE_1KUJI_PRODUCT_LIST_URL]
    default_interval_seconds = 6 * 60 * 60  # CLAUDE.md 8.5.4: 6時間に1回

    async def fetch(self, target_url: str) -> RawFetchResult:
        # ホワイトリスト形式: on-line.1kuji.com宛のリクエストは、完全一致する
        # ONLINE_1KUJI_PRODUCT_LIST_URL(商品一覧ページ)のみ許可する。それ以外
        # (ProductDetail.aspx等の個別詳細・応募ページを含む)は引き続き全面禁止
        # (モジュールdocstring「検証状況(2026-08-06)」参照)。
        if "on-line.1kuji.com" in target_url and target_url != ONLINE_1KUJI_PRODUCT_LIST_URL:
            raise FetchError(
                "on-line.1kuji.comへの自動アクセスは方針により禁止されています"
                f"(許可されているのは{ONLINE_1KUJI_PRODUCT_LIST_URL}のみ)"
            )

        try:
            # follow_redirects=True: app/collectors/markets/suruga_ya.pyと同じ理由
            # (2026-08-05、Collector稼働状況CLI導入時にsuruga-ya.jpの301で発覚)。
            # httpxはデフォルトでリダイレクトを追わないため統一して有効化する。
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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

        # 注意: "on-line.1kuji.com"は文字列として"1kuji.com"を含むため、この判定は
        # 必ず下の"1kuji.com"判定より先に行うこと(順序を入れ替えるとトップページ用の
        # _parse_top_page()に誤って渡り、「PICK UP ITEMを1件も抽出できませんでした」で
        # 失敗する)。
        if "on-line.1kuji.com" in raw.url:
            return self._parse_online_product_list(raw)
        if "1kuji.com" in raw.url and "bandaispirits" not in raw.url:
            return self._parse_top_page(raw)
        if "bandaispirits.co.jp" in raw.url:
            return [self._parse_bandaispirits_detail(raw)]

        raise ParseError(f"{raw.url}: 未対応のURLです(1kuji.com/on-line.1kuji.com/bandaispirits.co.jpのみ対応)")

    def _parse_top_page(self, raw: RawFetchResult) -> list[ParsedItem]:
        tree = HTMLParser(raw.html)
        items: list[ParsedItem] = []

        # 実データ確認済み(raw_1kuji_top.html): PICK UP ITEMは
        # section.pickupCol配下のdiv.swiper-slideに限定される。ここでCSSセレクタで
        # 範囲を絞ることで、ページ全体のラインナップ一覧(数百件規模)を誤って
        # 拾ってしまうことを構造的に防ぐ。
        for slide in tree.css("section.pickupCol div.swiper-slide"):
            anchor = slide.css_first("a")
            if anchor is None:
                continue

            href = anchor.attributes.get("href") or ""
            if not PRODUCT_PATH_PATTERN.match(href):
                continue
            product_url = urljoin(ICHIBAN_KUJI_TOP_URL, href)

            store_date, online_dt, product_name = _parse_pickup_slide(slide)
            if not product_name:
                # p.itemNameが無い(構造想定外の)slideはスキップする
                continue

            fulfillment_type = _fulfillment_type_for(store_date, online_dt)
            store_release_at = (
                datetime(store_date.year, store_date.month, store_date.day, tzinfo=JST)
                if store_date is not None
                else None
            )

            image_urls: list[str] = []
            img = anchor.css_first("img")
            if img is not None:
                src = img.attributes.get("src")
                if src:
                    # 実データ確認済み(raw_1kuji_top.html): 商品画像は同じ<a>内の<img src>。
                    image_urls.append(src)

            items.append(
                ParsedItem(
                    raw_title=product_name,
                    # トップページのPICK UP ITEMには価格情報が無い(Fixture注記参照)。
                    # 価格はbandaispirits.co.jpの詳細ページ等で別途補完する運用とする。
                    price=None,
                    image_urls=image_urls,
                    event_type=SupportedEventType.LOTTERY,
                    start_at=store_release_at or online_dt,
                    deadline_at=None,  # CLAUDE.md 1.1: on-line.1kuji.comが取得不可なため常にnull
                    announce_at=None,
                    purchase_limit_at=None,
                    apply_url=product_url,  # 実装仕様書9章: 組み立てられない場合はproduct_urlで代替
                    product_url=product_url,
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

    def _parse_online_product_list(self, raw: RawFetchResult) -> list[ParsedItem]:
        """on-line.1kuji.comの商品一覧ページ(ONLINE_1KUJI_PRODUCT_LIST_URL)から、
        現在販売中の商品(pid・商品名・価格・販売期間開始日時)を抽出する。

        実データ確認済み(raw_online_1kuji_productlist.html): 1商品 = 1つの
        `div.m-product-item`で、内部に`a.m-product-item__name`(商品名+
        pidを含むhref)・`.m-product-item__price`(税込価格)・
        `.m-product-item__sell-from`(販売開始日時)が含まれる。締切・当選発表は
        このページには存在せず取得できないため、常にNoneのままとする
        (モジュールdocstring参照)。
        """
        tree = HTMLParser(raw.html)
        items: list[ParsedItem] = []

        for product in tree.css("div.m-product-item"):
            name_anchor = product.css_first("a.m-product-item__name")
            if name_anchor is None:
                continue

            href = name_anchor.attributes.get("href") or ""
            pid_match = PID_PATTERN.search(href)
            if pid_match is None:
                continue
            pid = pid_match["pid"]

            product_name = name_anchor.text(strip=True)
            if not product_name:
                continue
            # apply_url/product_urlとしてProductDetail.aspxへのリンクを保存するのみで、
            # 本Collectorがこれをfetch()することは無い(ホワイトリスト方針、クラスdocstring参照)。
            product_url = urljoin(ONLINE_1KUJI_BASE_URL, href)

            price: Decimal | None = None
            price_node = product.css_first(".m-product-item__price")
            if price_node is not None:
                price_match = ONLINE_1KUJI_PRICE_PATTERN.search(price_node.text(strip=True))
                if price_match:
                    try:
                        price = Decimal(price_match["amount"].replace(",", ""))
                    except InvalidOperation:
                        price = None

            start_at: datetime | None = None
            sell_from_node = product.css_first(".m-product-item__sell-from")
            if sell_from_node is not None:
                sell_from_match = ONLINE_1KUJI_SELL_FROM_PATTERN.search(sell_from_node.text(strip=True))
                if sell_from_match:
                    start_at = datetime(
                        int(sell_from_match["y"]),
                        int(sell_from_match["mo"]),
                        int(sell_from_match["d"]),
                        int(sell_from_match["h"]),
                        int(sell_from_match["mi"]),
                        tzinfo=JST,
                    )

            image_urls: list[str] = []
            img = product.css_first(".m-product-item__image img")
            if img is not None:
                src = img.attributes.get("src")
                if src:
                    image_urls.append(urljoin(ONLINE_1KUJI_BASE_URL, src))

            items.append(
                ParsedItem(
                    raw_title=product_name,
                    price=price,
                    image_urls=image_urls,
                    event_type=SupportedEventType.LOTTERY,
                    start_at=start_at,
                    # このページには締切・当選発表の情報が存在しない(モジュールdocstring参照)。
                    # deadline_at=Noneのままにすることで、release_events.deadline_sourceは
                    # 既定値のDeadlineSource.UNKNOWNが自動的に適用される。
                    deadline_at=None,
                    announce_at=None,
                    purchase_limit_at=None,
                    apply_url=product_url,
                    product_url=product_url,
                    shop_name=None,
                    extra={"pid": pid},
                )
            )

        if not items:
            raise ParseError(f"{raw.url}: 商品一覧を1件も抽出できませんでした(構造変更の可能性)")

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

        # 実データ確認済み(raw_bandaispirits_detail.html): ページ先頭のtree.css_first("img")は
        # ヘッダーのBANDAI SPIRITSロゴ(/assets/img/logo_01.svg)を拾ってしまい、商品画像では
        # なかった(暫定実装のバグ)。実際の商品画像は`div.l_productsSlider`内の`img`(alt属性が
        # 商品名と一致)にまとまっており、先頭の1枚が商品全体の代表画像、以降は個別景品の画像。
        image_urls: list[str] = []
        slider = tree.css_first(".l_productsSlider")
        first_img = slider.css_first("img") if slider is not None else None
        if first_img is not None:
            src = first_img.attributes.get("src")
            if src:
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

"""IchibanKujiCollectorのparse()テスト。

トップページ(_parse_top_page)については、本番ConoHa VPSで実際に取得した生HTML
(tests/fixtures/raw_html/raw_1kuji_top.html)で動作検証済みであり、本ファイルの
TOP_PAGE_HTML は実データで確認した実際のDOM構造(section.pickupCol >
div.swiper-slide > a + div.txtCol > p.status/p.date/p.itemName)を最小限に
再現したものである(app/collectors/sources/ichiban_kuji.py のモジュールdocstring参照)。
raw_1kuji_top.htmlそのものを使った検証はtest_parse_top_page_against_raw_html_fixtureで行う。

bandaispirits.co.jpの商品詳細ページ側も、本番ConoHa VPSで実際に取得した生HTML
(tests/fixtures/raw_html/raw_bandaispirits_detail.html)で動作検証済み。価格/発売日/
商品名の抽出(ラベル行+値行のテキストパターンマッチ)は実データでもそのまま機能したが、
商品画像(image_urls)だけは実データで誤りが判明し修正した: tree.css_first("img")が
ページ最初の<img>(ヘッダーのBANDAI SPIRITSロゴ)を拾ってしまい、商品画像ではなかった。
実際の商品画像はdiv.l_productsSlider内にまとまっているため、このコンテナ配下のimgを
取得するよう修正した(app/collectors/sources/ichiban_kuji.pyのコメント参照)。
raw_bandaispirits_detail.htmlそのものを使った検証はtest_parse_bandaispirits_detail_against_raw_html_fixtureで行う。
BANDAISPIRITS_FREE_ITEM_HTML/BANDAISPIRITS_NORMAL_PRICE_ITEM_HTMLの2つは引き続き
Markdown変換済みテキストをもとにした合成HTMLのままである(無料配布・期間表記等、
今回取得した実データには含まれないケースの検証用として維持)。
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.collectors.base import FetchError, ParseError, RawFetchResult
from app.collectors.sources.ichiban_kuji import JST, ONLINE_1KUJI_PRODUCT_LIST_URL, IchibanKujiCollector
from app.domain.enums import FulfillmentType, RegionSource

RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_1kuji_top.html"
BANDAISPIRITS_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_bandaispirits_detail.html"
)
ONLINE_1KUJI_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_online_1kuji_productlist.html"
)

# 実データ確認済み(raw_1kuji_top.html)の実際のDOM構造を最小限に再現したもの。
# - 商品詳細へのリンクは絶対URLではなく相対パス"/products/{slug}"
# - 「店頭販売」「オンライン販売」はp.dateと同じ要素内ではなく、直前のp.statusに分離
# - 日付テキスト自体は「より発売予定」「より順次発売予定」の両方の表記がある
# - 商品名はp.itemNameに独立して格納されている(日付テキストの除去が不要)
TOP_PAGE_HTML = """
<html><body>
<section class="pickupCol"><h2>PICK UP ITEM</h2>
<div class="swiper-wrapper">
<div class="swiper-slide"><a href="/products/onep104"><img src="https://assets.1kuji.com/onep104.webp" /><div class="txtCol">
<p class="status shop">店頭販売</p>
<p class="date">2026年08月08日(土)より発売予定</p>
<p class="itemName">一番くじ ワンピース -エルバフ編- GIANT BASH!! Vol.2</p>
</div></a></div>
<div class="swiper-slide"><a href="/products/cutiestreet"><img src="https://assets.1kuji.com/cutiestreet.webp" /><div class="txtCol">
<p class="status shop">店頭販売</p>
<p class="date">2026年08月04日(火)より順次発売予定</p>
<p class="status online">オンライン販売</p>
<p class="date">2026年08月04日(火)17:00より販売開始予定</p>
<p class="itemName">一番くじ CUTIE STREET くじを手にする戦いなのです！</p>
</div></a></div>
<div class="swiper-slide"><a href="/products/kingdom6"><img src="https://assets.1kuji.com/kingdom6.webp" /><div class="txtCol">
<p class="status shop">店頭販売</p>
<p class="date">2026年07月31日(金)より順次発売予定</p>
<p class="status online">オンライン販売</p>
<p class="date">2026年08月03日(月)15:00より販売開始予定</p>
<p class="itemName">一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪</p>
</div></a></div>
<div class="swiper-slide"><a href="/products/petitcure"><img src="https://assets.1kuji.com/petitcure.webp" /><div class="txtCol">
<p class="status online">オンライン販売</p>
<p class="date">2026年06月24日(水)17:00より販売開始予定</p>
<p class="itemName">一番くじ ぷちきゅあ</p>
</div></a></div>
</div>
</section>
<section class="lineupCol"><h2>ラインナップ</h2>
<a href="/products/some-other-lineup-item">ラインナップ一覧の商品(日付情報なし)</a>
</section>
<a href="/products">商品一覧</a>
</body></html>
"""

# 実データ確認済み(raw_online_1kuji_productlist.html)の実際のDOM構造
# (div.m-product-item > a.m-product-item__name(href内にpid) /
#  .m-product-item__price(「¥NNN(税込)」) / .m-product-item__sell-from
#  (「YYYY年MM月DD日（曜）HH:MM」))を最小限に再現したもの。1件目は数字pid、
# 2件目は"sap_"接頭辞付きpid(実データで両方の形式が確認されている)。
ONLINE_PRODUCT_LIST_HTML = """
<html><body>
<div class="c-product-list__item-list">
  <div class="m-product-item">
    <div class="m-product-item__image">
      <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=9058260&bid=IP00004601">
        <img src="/Contents/ProductImages/0/9058260_LL.jpg">
      </a>
    </div>
    <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=9058260&bid=IP00004601" class="m-product-item__name m-product-item__link">
      一番くじ 進撃の巨人 ～選択と結果～
    </a>
    <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=9058260&bid=IP00004601" class="m-product-item__details m-product-item__link">
      <p class="m-product-item__price">&#165;700(税込)</p>
      <p class="m-product-item__sell-from">2026年08月05日（水）13:00</p>
    </a>
  </div>
  <div class="m-product-item">
    <div class="m-product-item__image">
      <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=sap_0000008524&bid=IP00002974">
        <img src="/Contents/ProductImages/0/sap_0000008524_LL.jpg">
      </a>
    </div>
    <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=sap_0000008524&bid=IP00002974" class="m-product-item__name m-product-item__link">
      一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪
    </a>
    <a href="/Form/Product/ProductDetail.aspx?shop=0&pid=sap_0000008524&bid=IP00002974" class="m-product-item__details m-product-item__link">
      <p class="m-product-item__price">&#165;790(税込)</p>
      <p class="m-product-item__sell-from">2026年08月03日（月）15:00</p>
    </a>
  </div>
</div>
</body></html>
"""

# tests/fixtures/html/bandaispirits_detail_20260803.md 注記1: 無料配布(通常の価格フォーマットでない)
BANDAISPIRITS_FREE_ITEM_HTML = """
<html><body>
<h1>PRODUCTS INFORMATION商品情報</h1>
<img src="https://www.bandaispirits.co.jp/images/uploads/product/image/10208/739e6ea0.jpg" alt="一番くじの一番くじ">
<h2>一番くじの一番くじ</h2>
<h3>商品詳細</h3>
<p>価格</p>
<p>イベント会場でアンケートにご回答いただいた方に無料配布</p>
<p>発売日</p>
<p>2024年02月23日(金・祝)～02月24日(土)</p>
<p>- 発売日（予定）は地域・店舗などによって異なる場合がございますのでご了承ください。</p>
</body></html>
"""

# 注記2の価格フォーマット(1回◯◯円(税◯％込))を、商品詳細ページの
# 価格/発売日ラベル構造に組み合わせた合成テストケース(Fixtureに実例が無いため)
BANDAISPIRITS_NORMAL_PRICE_ITEM_HTML = """
<html><body>
<h1>PRODUCTS INFORMATION商品情報</h1>
<h2>一番くじ しぐれうい 第2弾（仮）</h2>
<h3>商品詳細</h3>
<p>価格</p>
<p>1回790円(税10％込)</p>
<p>発売日</p>
<p>2026年09月05日(土)</p>
</body></html>
"""


def _raw(url: str, html: str) -> RawFetchResult:
    return RawFetchResult(url=url, status_code=200, html=html, fetched_at=datetime.now(tz=JST))


def test_parse_top_page_extracts_store_and_online_release_dates():
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    cutie = next(i for i in items if i.product_url.endswith("cutiestreet"))
    assert cutie.raw_title == "一番くじ CUTIE STREET くじを手にする戦いなのです！"
    assert cutie.extra["store_release_at"] == datetime(2026, 8, 4, tzinfo=JST)
    assert cutie.extra["online_release_at"] == datetime(2026, 8, 4, 17, 0, tzinfo=JST)
    assert cutie.extra["fulfillment_type"] == FulfillmentType.BOTH
    assert cutie.product_url == "https://1kuji.com/products/cutiestreet"
    assert cutie.image_urls == ["https://assets.1kuji.com/cutiestreet.webp"]


def test_parse_top_page_handles_store_only_item():
    """実データ確認済み(raw_1kuji_top.html): 店頭販売のみでオンライン販売の記載が無いケース。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    onep104 = next(i for i in items if i.product_url.endswith("onep104"))
    assert onep104.extra["store_release_at"] == datetime(2026, 8, 8, tzinfo=JST)
    assert onep104.extra["online_release_at"] is None
    assert onep104.extra["fulfillment_type"] == FulfillmentType.STORE_PICKUP


def test_parse_top_page_handles_online_only_item():
    """実データ確認済み(raw_1kuji_top.html): 店頭販売の記載が無く「オンライン販売」のみのケース。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    petitcure = next(i for i in items if i.product_url.endswith("petitcure"))
    assert petitcure.raw_title == "一番くじ ぷちきゅあ"
    assert petitcure.extra["store_release_at"] is None
    assert petitcure.extra["online_release_at"] == datetime(2026, 6, 24, 17, 0, tzinfo=JST)
    assert petitcure.extra["fulfillment_type"] == FulfillmentType.ONLINE_SHIPPING


def test_parse_top_page_ignores_links_outside_pickup_section():
    """section.pickupCol外のリンク(ラインナップ一覧、商品一覧等)は対象外。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    assert all(item.product_url != "https://1kuji.com/products" for item in items)
    assert all("some-other-lineup-item" not in item.product_url for item in items)
    assert len(items) == 4


def test_parse_top_page_raises_parse_error_when_no_items_found():
    collector = IchibanKujiCollector()

    with pytest.raises(ParseError):
        collector.parse(_raw(collector.target_urls[0], "<html><body>no pickup items here</body></html>"))


def test_parse_top_page_against_raw_html_fixture():
    """本番ConoHa VPSで実際に取得した生HTML(raw_1kuji_top.html)そのものに対する検証。
    Fixtureベースのテキストパターンマッチでは「PICK UP ITEMを1件も抽出できませんでした」で
    失敗していたが、CSSセレクタベースの実装により実際のPICK UP ITEM 15件が
    正しく抽出できることを確認する。"""
    collector = IchibanKujiCollector()
    html = RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    items = collector.parse(_raw(collector.target_urls[0], html))

    assert len(items) == 15

    cutie = next(i for i in items if i.product_url.endswith("cutiestreet"))
    assert cutie.raw_title == "一番くじ CUTIE STREET くじを手にする戦いなのです！"
    assert cutie.extra["fulfillment_type"] == FulfillmentType.BOTH
    assert cutie.extra["store_release_at"] == datetime(2026, 8, 4, tzinfo=JST)
    assert cutie.extra["online_release_at"] == datetime(2026, 8, 4, 17, 0, tzinfo=JST)

    onep104 = next(i for i in items if i.product_url.endswith("onep104"))
    assert onep104.extra["fulfillment_type"] == FulfillmentType.STORE_PICKUP
    assert onep104.extra["store_release_at"] == datetime(2026, 8, 8, tzinfo=JST)
    assert onep104.extra["online_release_at"] is None

    petitcure = next(i for i in items if i.product_url.endswith("petitcure"))
    assert petitcure.extra["fulfillment_type"] == FulfillmentType.ONLINE_SHIPPING
    assert petitcure.extra["store_release_at"] is None
    assert petitcure.extra["online_release_at"] == datetime(2026, 6, 24, 17, 0, tzinfo=JST)

    assert all(item.product_url.startswith("https://1kuji.com/products/") for item in items)


# --- on-line.1kuji.com商品一覧ページ(ProductList.aspx)のテスト(2026-08-06追加) ---


def test_parse_online_product_list_extracts_name_price_pid_and_sell_from():
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, ONLINE_PRODUCT_LIST_HTML))

    shingeki = next(i for i in items if i.extra["pid"] == "9058260")
    assert shingeki.raw_title == "一番くじ 進撃の巨人 ～選択と結果～"
    assert shingeki.price == Decimal("700")
    assert shingeki.start_at == datetime(2026, 8, 5, 13, 0, tzinfo=JST)
    assert shingeki.image_urls == ["https://on-line.1kuji.com/Contents/ProductImages/0/9058260_LL.jpg"]


def test_parse_online_product_list_supports_sap_prefixed_pid():
    """実データ確認済み: pidは数字のみ("9058260")と"sap_"接頭辞付き("sap_0000008524")の
    両方の形式がある。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, ONLINE_PRODUCT_LIST_HTML))

    kingdom = next(i for i in items if i.extra["pid"] == "sap_0000008524")
    assert kingdom.raw_title == "一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪"
    assert kingdom.price == Decimal("790")
    assert kingdom.start_at == datetime(2026, 8, 3, 15, 0, tzinfo=JST)


def test_parse_online_product_list_builds_absolute_apply_url_without_fetching_it():
    """apply_url/product_urlにはProductDetail.aspxへの絶対URLを保存するが、
    本Collectorがこのcollector自身でfetchすることは無い(ホワイトリスト方針)。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, ONLINE_PRODUCT_LIST_HTML))

    shingeki = next(i for i in items if i.extra["pid"] == "9058260")
    assert (
        shingeki.apply_url
        == "https://on-line.1kuji.com/Form/Product/ProductDetail.aspx?shop=0&pid=9058260&bid=IP00004601"
    )
    assert shingeki.product_url == shingeki.apply_url


def test_parse_online_product_list_deadline_is_always_none():
    """このページには締切・当選発表の情報が存在しないため、常にNoneのままにする
    (deadline_source列は既存のデフォルト値UNKNOWNが自動的に適用される)。"""
    collector = IchibanKujiCollector()

    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, ONLINE_PRODUCT_LIST_HTML))

    assert all(item.deadline_at is None for item in items)
    assert all(item.announce_at is None for item in items)


def test_parse_online_product_list_raises_parse_error_when_no_items_found():
    collector = IchibanKujiCollector()

    with pytest.raises(ParseError):
        collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, "<html><body>no products here</body></html>"))


def test_parse_online_product_list_against_raw_html_fixture():
    """CLAUDE.md 1.1「on-line.1kuji.com商品一覧ページの取得可否確認」に対応する検証。
    実際に取得した生HTML(raw_online_1kuji_productlist.html)には、確認時点で
    販売中の全22商品が含まれており、コード変更無しで全件正しく抽出できることを確認する。
    """
    collector = IchibanKujiCollector()
    html = ONLINE_1KUJI_PRODUCT_LIST_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, html))

    assert len(items) == 22
    # pidが全件ユニークであること(重複抽出していないこと)
    assert len({item.extra["pid"] for item in items}) == 22
    assert all(item.price is not None for item in items)
    assert all(item.start_at is not None for item in items)
    assert all(item.deadline_at is None for item in items)
    assert all(item.apply_url.startswith("https://on-line.1kuji.com/Form/Product/ProductDetail.aspx") for item in items)

    shingeki = next(i for i in items if i.extra["pid"] == "9058260")
    assert shingeki.raw_title == "一番くじ 進撃の巨人 ～選択と結果～"
    assert shingeki.price == Decimal("700")
    assert shingeki.start_at == datetime(2026, 8, 5, 13, 0, tzinfo=JST)

    kingdom = next(i for i in items if i.extra["pid"] == "sap_0000008524")
    assert kingdom.raw_title == "一番くじ 春秋戦国大戦キングダム The Animation 知と武の両輪"


def test_normalize_online_product_list_uses_online_shipping_fulfillment():
    """on-line.1kuji.comは通販専用チャネルのため、fulfillment_typeは常にONLINE_SHIPPING。"""
    collector = IchibanKujiCollector()
    items = collector.parse(_raw(ONLINE_1KUJI_PRODUCT_LIST_URL, ONLINE_PRODUCT_LIST_HTML))

    normalized = collector.normalize(items)

    assert all(n.fulfillment_type == FulfillmentType.ONLINE_SHIPPING for n in normalized)
    assert all(n.region_source == RegionSource.UNKNOWN for n in normalized)


def test_parse_bandaispirits_detail_handles_non_standard_free_distribution_price():
    """Fixture注記1: 無料配布ケースでは価格を無理にnullにせず生テキストをextraに残す。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw("https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=ichibankuji", BANDAISPIRITS_FREE_ITEM_HTML)
    )[0]

    assert item.raw_title == "一番くじの一番くじ"
    assert item.price is None
    assert item.extra["price_raw_text"] == "イベント会場でアンケートにご回答いただいた方に無料配布"


def test_parse_bandaispirits_detail_handles_date_range_by_taking_start_date_only():
    """Fixture注記4: 期間表記(〜MM月DD日)は開始日のみ採用しraw textをextraに残す(要確認事項)。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw("https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=ichibankuji", BANDAISPIRITS_FREE_ITEM_HTML)
    )[0]

    assert item.start_at == datetime(2024, 2, 23, tzinfo=JST)
    assert item.extra["release_date_raw_text"] == "2024年02月23日(金・祝)～02月24日(土)"


def test_parse_bandaispirits_detail_extracts_normal_price_format():
    """Fixture注記2の価格正規表現(1回◯◯円(税◯％込))が正しく数値化されること。"""
    collector = IchibanKujiCollector()

    item = collector.parse(
        _raw(
            "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=shigureui2",
            BANDAISPIRITS_NORMAL_PRICE_ITEM_HTML,
        )
    )[0]

    assert item.raw_title == "一番くじ しぐれうい 第2弾（仮）"
    assert item.price == Decimal("790")
    assert "price_raw_text" not in item.extra
    assert item.start_at == datetime(2026, 9, 5, tzinfo=JST)


def test_parse_bandaispirits_detail_against_raw_html_fixture():
    """CLAUDE.md 3節「bandaispirits.co.jpの商品詳細ページの生HTML取得・検証」に対応する検証。
    本番ConoHa VPSで実際に取得した一番くじ(鬼滅の刃、通常の一番くじ商品)の商品詳細ページ
    そのものに対する検証。商品名・価格・発売日はFixtureベースの推測実装のまま追加の
    修正無しで正しく抽出できたが、image_urlsだけはページ最初のimgがヘッダーロゴを
    指しており誤りだったため修正した(モジュールdocstring参照)。"""
    collector = IchibanKujiCollector()
    html = BANDAISPIRITS_RAW_HTML_FIXTURE_PATH.read_text(encoding="utf-8")

    item = collector.parse(
        _raw(
            "https://www.bandaispirits.co.jp/products/search/detail.php?prd_id=kimetsu29&grp_id=9999",
            html,
        )
    )[0]

    assert item.raw_title == "一番くじ 鬼滅の刃～姉の仇～"
    assert item.price == Decimal("790")
    assert "price_raw_text" not in item.extra
    assert item.start_at == datetime(2025, 11, 29, tzinfo=JST)
    assert item.extra["release_date_raw_text"] == "2025年11月29日(土)より順次発売予定"
    # 修正前はヘッダーロゴ(/assets/img/logo_01.svg)を拾っていた
    assert item.image_urls == [
        "https://assets.1kuji.com/uploads/product/image/10559/f3aaa250-ded5-480b-b5f9-2f393b67ad1c.jpg"
    ]


def test_normalize_uses_extracted_fulfillment_type_and_unknown_region():
    collector = IchibanKujiCollector()
    parsed_items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))

    normalized = collector.normalize(parsed_items)

    cutie = next(n for n in normalized if n.parsed.product_url.endswith("cutiestreet"))
    assert cutie.fulfillment_type == FulfillmentType.BOTH
    assert cutie.region_source == RegionSource.UNKNOWN
    assert cutie.category_hint == "一番くじ"


def test_validate_does_not_require_price_because_top_page_never_has_it():
    """1kuji.comのPICK UP ITEMには価格情報が無いため、基底クラスの価格必須チェックを緩和している。"""
    collector = IchibanKujiCollector()
    parsed_items = collector.parse(_raw(collector.target_urls[0], TOP_PAGE_HTML))
    normalized = collector.normalize(parsed_items)

    for item in normalized:
        assert item.parsed.price is None
        assert collector.validate(item) == []


async def test_run_end_to_end_against_top_page_fixture_pattern(monkeypatch):
    """run()はtarget_urls(1kuji.comトップページ + on-line.1kuji.com商品一覧)を
    両方巡回するため、target_urlごとに対応するHTMLを返すfake_fetchにする
    (2026-08-06、on-line.1kuji.com商品一覧をtarget_urlsに追加した際に対応)。"""
    collector = IchibanKujiCollector()

    async def fake_fetch(target_url: str) -> RawFetchResult:
        if "on-line.1kuji.com" in target_url:
            return _raw(target_url, ONLINE_PRODUCT_LIST_HTML)
        return _raw(target_url, TOP_PAGE_HTML)

    monkeypatch.setattr(collector, "fetch", fake_fetch)

    result = await collector.run()

    assert result.success_count == 4 + 2  # トップページ4件 + 商品一覧2件(合成HTML)
    assert result.error_count == 0


def test_target_urls_only_reference_product_list_page_on_online_1kuji_com():
    """CLAUDE.md 1.1(2026-08-06更新): on-line.1kuji.comへの自動リクエストは
    ONLINE_1KUJI_PRODUCT_LIST_URL(商品一覧ページ)のみホワイトリストで許可されている。
    それ以外のon-line.1kuji.comパス(個別詳細・応募ページ等)は禁止のまま。
    """
    collector = IchibanKujiCollector()

    online_1kuji_urls = [url for url in collector.target_urls if "on-line.1kuji.com" in url]
    assert online_1kuji_urls == [ONLINE_1KUJI_PRODUCT_LIST_URL]


async def test_fetch_raises_fetch_error_for_on_line_1kuji_com_detail_page():
    """個別詳細・応募ページ(ProductDetail.aspx等)は引き続き全面禁止(Bot対策により
    取得不可なため)。"""
    collector = IchibanKujiCollector()

    with pytest.raises(FetchError):
        await collector.fetch("https://on-line.1kuji.com/some/apply/path")

    with pytest.raises(FetchError):
        await collector.fetch("https://on-line.1kuji.com/Form/Product/ProductDetail.aspx?pid=123")


async def test_fetch_raises_fetch_error_for_product_list_url_with_extra_query():
    """ホワイトリストは完全一致のみを許可する。クエリパラメータが付加された
    ProductList.aspxのバリエーションであっても、完全一致しない限り禁止する
    (厳格なホワイトリストであることの確認)。"""
    collector = IchibanKujiCollector()

    with pytest.raises(FetchError):
        await collector.fetch(f"{ONLINE_1KUJI_PRODUCT_LIST_URL}?extra=1")

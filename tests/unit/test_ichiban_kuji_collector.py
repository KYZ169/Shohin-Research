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
from app.collectors.sources.ichiban_kuji import JST, IchibanKujiCollector
from app.domain.enums import FulfillmentType, RegionSource

RAW_HTML_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_1kuji_top.html"
BANDAISPIRITS_RAW_HTML_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "raw_html" / "raw_bandaispirits_detail.html"
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
    collector = IchibanKujiCollector()

    async def fake_fetch(target_url: str) -> RawFetchResult:
        return _raw(target_url, TOP_PAGE_HTML)

    monkeypatch.setattr(collector, "fetch", fake_fetch)

    result = await collector.run()

    assert result.success_count == 4
    assert result.error_count == 0


def test_target_urls_never_reference_on_line_1kuji_com():
    """CLAUDE.mdタスク4着手前チェックリスト: on-line.1kuji.comへの自動リクエスト禁止。"""
    collector = IchibanKujiCollector()

    assert all("on-line.1kuji.com" not in url for url in collector.target_urls)


async def test_fetch_raises_fetch_error_for_on_line_1kuji_com():
    collector = IchibanKujiCollector()

    with pytest.raises(FetchError):
        await collector.fetch("https://on-line.1kuji.com/some/apply/path")
